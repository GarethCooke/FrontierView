"""Schema contract test: schema advertised to the model == schema validated at dispatch."""

from agent.tools import TOOLS, _INPUT_MODELS, _build_schema


def test_schema_contract_all_tools():
    """For every tool, the schema in TOOLS must equal the schema generated from its Pydantic model."""
    tool_map = {t["name"]: t["input_schema"] for t in TOOLS}

    assert set(tool_map) == set(_INPUT_MODELS), (
        f"Tool name mismatch.\n"
        f"  In TOOLS but not _INPUT_MODELS: {set(tool_map) - set(_INPUT_MODELS)}\n"
        f"  In _INPUT_MODELS but not TOOLS: {set(_INPUT_MODELS) - set(tool_map)}"
    )

    for name, model_cls in _INPUT_MODELS.items():
        advertised = tool_map[name]
        derived = _build_schema(model_cls)
        assert advertised == derived, (
            f"Schema mismatch for tool '{name}'.\n"
            f"  Advertised: {advertised}\n"
            f"  Derived:    {derived}"
        )


def test_no_hand_written_schema_copy():
    """Confirms the TOOLS list is built from _INPUT_MODELS, not maintained separately."""
    from agent.tools import _SCHEMAS

    for name, schema in _SCHEMAS.items():
        model_cls = _INPUT_MODELS[name]
        assert schema == _build_schema(model_cls), (
            f"_SCHEMAS['{name}'] diverged from _build_schema({model_cls.__name__})"
        )
