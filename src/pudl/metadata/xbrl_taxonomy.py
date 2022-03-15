"""XBRL prototype structures."""
from typing import Dict, ForwardRef, List, Optional, Set

from arelle.ModelInstanceObject import ModelFact
from pydantic import root_validator, validator

from .classes import Base, HttpUrl


def _extract_name(qname: str) -> str:
    """Get name from qname."""
    return qname.split(':')[1]


def _fact_to_dict(fact: ModelFact) -> Dict:
    """Create a dictionary describing a single fact."""
    fact_dict = {
        "value": fact.value
    }

    if fact.context.qnameDims:
        fact_dict["dims"] = {_extract_name(str(qname)): dim.propertyView[1]
                             for qname, dim in fact.context.qnameDims.items()}

    if fact.context.isInstantPeriod:
        fact_dict["date"] = fact.context.instantDatetime
    else:
        fact_dict["start_date"] = fact.context.startDatetime
        fact_dict["end_date"] = fact.context.endDatetime

    print(fact_dict)

    return fact_dict


Concept = ForwardRef('Concept')


class Concept(Base):
    """Concept."""

    name: str
    label: str
    references: Optional[str]
    label: str
    type: str  # noqa: A003
    child_concepts: Optional[List[Concept]] = None

    @root_validator(pre=True)
    def map_label(cls, values):
        """Change label name."""
        if 'pref.Label' in values:
            values['label'] = values.pop('pref.Label')

        return values

    @classmethod
    def from_list(cls, concept_list: List):
        """Construct from list."""
        if concept_list[0] != 'concept':
            raise ValueError("First element should be 'concept'")

        if not isinstance(concept_list[1], dict) or not isinstance(concept_list[2], dict):
            raise TypeError("Second 2 elements should be dicts")

        return cls(
            **concept_list[1], **concept_list[2],
            child_concepts=[Concept.from_list(concept) for concept in concept_list[3:]]
        )

    def get_facts(self, fact_dict: Dict[str, Set[ModelFact]]):
        """Create structured dict of facts."""
        if len(self.child_concepts) > 0:
            return {_extract_name(concept.name): concept.get_facts(fact_dict)
                    for concept in self.child_concepts}

        facts = fact_dict.get(self.name)
        if not facts:
            return None

        return [_fact_to_dict(fact) for fact in facts]


Concept.update_forward_refs()


class LinkRole(Base):
    """LinkRole."""

    role: HttpUrl
    definition: str
    concepts: 'Concept'

    @classmethod
    def from_list(cls, linkrole_list: List) -> 'Concept':
        """Construct from list."""
        if linkrole_list[0] != 'linkRole':
            raise ValueError("First element should be 'linkRole'")

        if not isinstance(linkrole_list[1], dict):
            raise TypeError("Second element should be dicts")

        return cls(**linkrole_list[1], concepts=Concept.from_list(linkrole_list[3]))


class Taxonomy(Base):
    """Taxonomy."""

    roles: List['LinkRole']

    @validator('roles', pre=True)
    def validate_taxonomy(cls, roles):
        """Create children."""
        taxonomy = [LinkRole.from_list(role) for role in roles]
        return taxonomy

    def get_page(self, page_num: int, section: str = None) -> List[LinkRole]:
        """Helper to get tables from page."""
        roles = []
        page_id = str(page_num).zfill(3)
        if section:
            page_id += section

        for role in self.roles:
            if role.definition.startswith(page_id):
                roles.append(role)

        return roles

    def get_fact_table(self, page_num: int, fact_dict, section: str = None):
        """Create structured fact table."""
        return [role.concepts.get_facts(fact_dict)
                for role in self.get_page(page_num, section)]
