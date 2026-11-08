"""The agents each computer gave last, kept for the computers that are away."""

import bazaar_contrib_storage as s
from bazaar_contrib_schema import Migration, ModuleSchema

AGENTS = s.Table(
    name="agents",
    columns=(
        s.ColumnDefinition(name="computer_id", type="text"),
        s.ColumnDefinition(name="agent_id", type="text"),
        s.ColumnDefinition(name="name", type="text"),
        s.ColumnDefinition(name="config", type="text"),
        s.ColumnDefinition(name="fetched_at_ms", type="integer"),
    ),
    primary_key=("computer_id", "agent_id"),
)

MODULE = ModuleSchema(
    tables=("agents",),
    migrations=(
        Migration(version=1, name="agents", operations=(s.CreateTable(table=AGENTS),)),
    ),
)
