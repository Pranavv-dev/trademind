"""Tests for the Zerodha instrument master."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.data.feeds.instruments import InstrumentMaster


SAMPLE_INSTRUMENTS = [
    {
        "instrument_token": 738561,
        "exchange_token": 2885,
        "tradingsymbol": "RELIANCE",
        "name": "RELIANCE INDUSTRIES",
        "exchange": "NSE",
        "instrument_type": "EQ",
        "lot_size": 1,
        "tick_size": 0.05,
        "expiry": None,
        "strike": 0,
    },
    {
        "instrument_token": 408065,
        "exchange_token": 1594,
        "tradingsymbol": "INFY",
        "name": "INFOSYS",
        "exchange": "NSE",
        "instrument_type": "EQ",
        "lot_size": 1,
        "tick_size": 0.05,
        "expiry": None,
        "strike": 0,
    },
    {
        "instrument_token": 895745,
        "exchange_token": 3499,
        "tradingsymbol": "TCS",
        "name": "TATA CONSULTANCY SERV",
        "exchange": "NSE",
        "instrument_type": "EQ",
        "lot_size": 1,
        "tick_size": 0.05,
        "expiry": None,
        "strike": 0,
    },
    {
        "instrument_token": 12345678,
        "exchange_token": 48765,
        "tradingsymbol": "NIFTY2530126000CE",
        "name": "NIFTY",
        "exchange": "NFO",
        "instrument_type": "CE",
        "lot_size": 25,
        "tick_size": 0.05,
        "expiry": "2025-03-01",
        "strike": 26000,
    },
    {
        "instrument_token": 12345679,
        "exchange_token": 48766,
        "tradingsymbol": "NIFTY2530126000PE",
        "name": "NIFTY",
        "exchange": "NFO",
        "instrument_type": "PE",
        "lot_size": 25,
        "tick_size": 0.05,
        "expiry": "2025-03-01",
        "strike": 26000,
    },
    {
        "instrument_token": 12345680,
        "exchange_token": 48767,
        "tradingsymbol": "NIFTY2530126500CE",
        "name": "NIFTY",
        "exchange": "NFO",
        "instrument_type": "CE",
        "lot_size": 25,
        "tick_size": 0.05,
        "expiry": "2025-03-01",
        "strike": 26500,
    },
    {
        "instrument_token": 999999,
        "exchange_token": 3906,
        "tradingsymbol": "RELIANCE",
        "name": "RELIANCE INDUSTRIES",
        "exchange": "BSE",
        "instrument_type": "EQ",
        "lot_size": 1,
        "tick_size": 0.05,
        "expiry": None,
        "strike": 0,
    },
]


@pytest.fixture
def master():
    m = InstrumentMaster()
    return m


@pytest.fixture
def loaded_master():
    m = InstrumentMaster()
    m._instruments = SAMPLE_INSTRUMENTS
    m._by_symbol = {}
    m._by_token = {}
    for inst in SAMPLE_INSTRUMENTS:
        key = f"{inst['exchange']}:{inst['tradingsymbol']}"
        m._by_symbol[key] = inst
        token = inst.get("instrument_token")
        if token:
            m._by_token[token] = inst
    return m


class TestInstrumentMasterInit:
    def test_not_loaded_by_default(self, master):
        assert master.loaded is False
        assert master.count == 0

    def test_loaded_after_data(self, loaded_master):
        assert loaded_master.loaded is True
        assert loaded_master.count == len(SAMPLE_INSTRUMENTS)


class TestInstrumentMasterLoad:
    @pytest.mark.asyncio
    async def test_load_success(self, master):
        mock_kite = MagicMock()
        mock_kite.instruments.return_value = SAMPLE_INSTRUMENTS

        count = await master.load(mock_kite)
        assert count == len(SAMPLE_INSTRUMENTS)
        assert master.loaded is True
        assert master._last_updated is not None

    @pytest.mark.asyncio
    async def test_load_no_api_key(self, master):
        with patch("app.data.feeds.instruments.settings") as mock_settings:
            mock_settings.kite_api_key = ""
            count = await master.load()
            assert count == 0
            assert master.loaded is False

    @pytest.mark.asyncio
    async def test_load_api_error(self, master):
        mock_kite = MagicMock()
        mock_kite.instruments.side_effect = Exception("API down")

        count = await master.load(mock_kite)
        assert count == 0
        assert master.loaded is False


class TestGetToken:
    def test_get_token_found(self, loaded_master):
        token = loaded_master.get_token("NSE", "RELIANCE")
        assert token == 738561

    def test_get_token_not_found(self, loaded_master):
        token = loaded_master.get_token("NSE", "NONEXISTENT")
        assert token is None

    def test_get_token_different_exchange(self, loaded_master):
        token = loaded_master.get_token("BSE", "RELIANCE")
        assert token == 999999


class TestGetTokens:
    def test_batch_lookup(self, loaded_master):
        symbols = [("NSE", "RELIANCE"), ("NSE", "INFY"), ("NSE", "NONEXISTENT")]
        result = loaded_master.get_tokens(symbols)
        assert "RELIANCE" in result
        assert "INFY" in result
        assert "NONEXISTENT" not in result
        assert result["RELIANCE"] == 738561
        assert result["INFY"] == 408065


class TestGetSymbol:
    def test_reverse_lookup(self, loaded_master):
        result = loaded_master.get_symbol(738561)
        assert result == ("NSE", "RELIANCE")

    def test_reverse_lookup_not_found(self, loaded_master):
        result = loaded_master.get_symbol(999)
        assert result is None


class TestSearch:
    def test_search_by_symbol(self, loaded_master):
        results = loaded_master.search("REL", exchange="NSE")
        assert len(results) == 1
        assert results[0]["symbol"] == "RELIANCE"

    def test_search_by_name(self, loaded_master):
        results = loaded_master.search("INFOSYS", exchange="NSE")
        assert len(results) == 1
        assert results[0]["symbol"] == "INFY"

    def test_search_case_insensitive(self, loaded_master):
        results = loaded_master.search("reliance", exchange="NSE")
        assert len(results) == 1

    def test_search_limit(self, loaded_master):
        results = loaded_master.search("", exchange="NSE", limit=2)
        assert len(results) == 2

    def test_search_no_results(self, loaded_master):
        results = loaded_master.search("ZZZZ", exchange="NSE")
        assert len(results) == 0

    def test_search_different_exchange(self, loaded_master):
        results = loaded_master.search("RELIANCE", exchange="BSE")
        assert len(results) == 1
        assert results[0]["exchange"] == "BSE"


class TestNFOOptions:
    def test_get_all_nfo_options(self, loaded_master):
        results = loaded_master.get_nfo_options("NIFTY")
        assert len(results) == 3  # 2 CE + 1 PE at two strikes

    def test_filter_by_option_type(self, loaded_master):
        results = loaded_master.get_nfo_options("NIFTY", option_type="CE")
        assert len(results) == 2
        assert all(r["type"] == "CE" for r in results)

    def test_filter_by_expiry(self, loaded_master):
        results = loaded_master.get_nfo_options("NIFTY", expiry="2025-03-01")
        assert len(results) == 3

    def test_filter_by_expiry_no_match(self, loaded_master):
        results = loaded_master.get_nfo_options("NIFTY", expiry="2025-04-01")
        assert len(results) == 0

    def test_sorted_by_expiry_and_strike(self, loaded_master):
        results = loaded_master.get_nfo_options("NIFTY")
        strikes = [r["strike"] for r in results]
        # Both CE at 26000 and PE at 26000 come before CE at 26500
        assert strikes[-1] == 26500

    def test_unknown_underlying(self, loaded_master):
        results = loaded_master.get_nfo_options("BANKNIFTY")
        assert len(results) == 0
