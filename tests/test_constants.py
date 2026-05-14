from custom_components.neosmartblinds import const


def test_defaults_present():
    assert isinstance(const.DEFAULT_IO_TIMEOUT, int)
    assert const.DEFAULT_COMMAND_BACKOFF > 0
    assert const.DEFAULT_COMMAND_AGGREGATION_PERIOD > 0
    assert const.DEFAULT_RETRY_COUNT >= 0
    assert const.DEFAULT_RETRY_DELAY >= 0
