"""Implements pipeline for processing EPA CEMS dataset."""
import itertools
import logging
import os
from typing import Any

import pandas as pd
import prefect
from prefect import task, unmapped

import pudl
from pudl import constants as pc
from pudl.constants import PUDL_TABLES
from pudl.dfc import DataFrameCollection
from pudl.extract.epacems import EpaCemsDatastore, EpaCemsPartition
from pudl.workflow.dataset_pipeline import DatasetPipeline
from pudl.workspace.datastore import Datastore

logger = logging.getLogger(__name__)


def _validate_params_partition(etl_params_og, tables):
    # if there is a `partition` in the package settings..
    partition_dict = {}
    try:
        partition_dict = etl_params_og['partition']
        # it should be a dictionary with tables (keys) and partitions (values)
        # so for each table, grab the list of the corresponding partition.
        for table in tables:
            try:
                for part in partition_dict[table]:
                    if part not in etl_params_og.keys():
                        raise AssertionError('Partion not recognized')
            except KeyError:
                pass
    except KeyError:
        partition_dict['partition'] = None
    return(partition_dict)


@task(target="epacems-{partition}", task_run_name="epacems-{partition}")  # noqa: FS003
def epacems_process_partition(
        partition: EpaCemsPartition,
        plant_utc_offset: pd.DataFrame,
        datastore: EpaCemsDatastore) -> DataFrameCollection:
    """Runs extract and transform phases for a given epacems partition."""
    logger = prefect.context.get("logger")
    logger.info(f'Processing epacems partition {partition}')

    df = pudl.extract.epacems.extract_epacems(partition, datastore)
    df = pudl.transform.epacems.transform_epacems(df, plant_utc_offset)

    # Add state and year to dataframe
    df["year"] = partition.year
    df["state"] = partition.state

    output_path = os.path.join(prefect.context.pudl_settings["parquet_dir"], "epacems")
    pudl.load.parquet.epacems_to_parquet(df, output_path)
    return DataFrameCollection()  # return empty DFC because everything is on disk


class EpaCemsPipeline(DatasetPipeline):
    """Runs epacems tasks."""

    DATASET = 'epacems'

    def __init__(self, *args: Any, **kwargs: Any):
        """Initializes epacems pipeline, hooks it to the existing eia pipeline.

        epacems depends on the plants_entity_eia table that is generated by the
        EiaPipeline. If epacems is run, it will pull this table from the existing
        eia pipeline.

        Args:
          eia_pipeline: instance of EiaPipeline that holds the plants_entity_eia
            table.
        """
        super().__init__(*args, **kwargs)

    @classmethod
    def validate_params(cls, etl_params):
        """Validate and normalize epacems parameters."""
        epacems_dict = {
            "epacems_years": etl_params.get("epacems_years", []),
            "epacems_states": etl_params.get("epacems_states", []),
        }
        if epacems_dict["epacems_states"] and epacems_dict["epacems_states"][0].lower() == "all":
            epacems_dict["epacems_states"] = sorted(pc.cems_states.keys())

        # CEMS is ALWAYS going to be partitioned by year and state. This means we
        # are functinoally removing the option to not partition or partition
        # another way. Nonetheless, we are adding it in here because we still need
        # to know what the partitioning is like for the metadata generation
        # (it treats partitioned tables differently).
        epacems_dict['partition'] = {'hourly_emissions_epacems':
                                     ['epacems_years', 'epacems_states']}
        # this is maybe unnecessary because we are hardcoding the partitions, but
        # we are still going to validate that the partitioning is
        epacems_dict['partition'] = _validate_params_partition(
            epacems_dict, [PUDL_TABLES["epacems"]])
        if not epacems_dict['partition']:
            raise AssertionError(
                'No partition found for EPA CEMS.'
                'EPA CEMS requires either states or years as a partion'
            )

        if not epacems_dict['epacems_years'] or not epacems_dict['epacems_states']:
            return None
        else:
            return epacems_dict

    def build(self, params):
        """Add epacems tasks to the flow."""
        if not self.all_params_present(params, ['epacems_states', 'epacems_years']):
            return None
        with self.flow as flow:
            plants = pudl.transform.epacems.load_plant_utc_offset()

            # Wait to build CEMS until EIA is done if EIA is in the settings file.
            # If EIA is not in the settings file, go ahead and build CEMS on its own.
            if "eia" in prefect.context.get("dataset_names", {}):
                dfs_to_sqlite_task = flow.get_tasks(name="dfs_to_sqlite")
                logger.info("Setting EIA as a dependency of CEMS.")
                plants.set_dependencies(upstream_tasks=[dfs_to_sqlite_task])

            partitions = [
                EpaCemsPartition(year=y, state=s)
                for y, s in itertools.product(params["epacems_years"], params["epacems_states"])]

            ds = EpaCemsDatastore(Datastore.from_prefect_context())
            epacems_process_partition.map(
                partitions,
                plant_utc_offset=unmapped(plants),
                datastore=unmapped(ds))
