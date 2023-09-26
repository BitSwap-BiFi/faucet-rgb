"""Tests for basic API."""
import rgb_lib

from faucet_rgb.utils.wallet import get_sha256_hex
from tests import fixture_get_app  # pylint:disable=unused-import
from tests.utils import (OPERATOR_HEADERS, USER_HEADERS, check_receive_asset,
                         check_requests_left, prepare_user_wallets)


def test_control_assets(get_app):
    """Test /control/assets endpoint."""
    app = get_app()
    client = app.test_client()
    res = client.get("/control/assets", headers=OPERATOR_HEADERS)
    assert res.status_code == 200
    assert len(res.data) != 0


def test_receive_config(get_app):
    """Test /receive/config endpoint."""
    app = get_app()
    bitcoin_network = getattr(rgb_lib.BitcoinNetwork, "REGTEST")
    user_xpub = rgb_lib.generate_keys(bitcoin_network).xpub
    wallet_id = get_sha256_hex(user_xpub)
    check_requests_left(app, wallet_id, {"group_1": 1})


def test_receive_asset(get_app):
    """Test /receive/asset/<wallet_id>/<blinded_utxo> endpoint."""
    app = get_app()
    user = prepare_user_wallets(app, 1)[0]
    check_receive_asset(app, user, None, 200, None)

    # check requests with xPub as wallet ID are denied
    wallet_id = user["xpub"]
    wallet = user["wallet"]
    _ = wallet.create_utxos(user["online"], True, 1, None,
                            app.config["FEE_RATE"])
    blind_data = wallet.blind_receive(None, None, None,
                                      app.config["CONSIGNMENT_ENDPOINTS"], 1)
    group_query = ""
    client = app.test_client()
    resp = client.get(
        f"/receive/asset/{wallet_id}/{blind_data.recipient_id}{group_query}",
        headers=USER_HEADERS,
    )
    assert resp.status_code == 403
