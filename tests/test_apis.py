"""Tests for APIs."""

import random
import time
import uuid

import rgb_lib
from rgb_lib._rgb_lib.rgb_lib import TransferKind

from faucet_rgb import Request, scheduler
from faucet_rgb.utils.wallet import get_sha256_hex
from tests.utils import (
    BAD_HEADERS, ISSUE_AMOUNT, OPERATOR_HEADERS, USER_HEADERS,
    add_fake_request, check_receive_asset, check_requests_left,
    create_and_blind, generate, prepare_user_wallets, wait_refresh,
    wait_scheduler_processing)


def test_control_assets(get_app):
    """Test /control/assets endpoint."""
    api = '/control/assets'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(api, headers=USER_HEADERS)
    assert res.status_code == 401

    # success
    res = client.get(api, headers=OPERATOR_HEADERS)
    assert res.status_code == 200
    assert 'assets' in res.json
    assert len(res.json['assets']) == 2
    first_asset = next(iter(res.json['assets']))
    assert 'balance' in res.json['assets'][first_asset]
    assert 'name' in res.json['assets'][first_asset]
    assert 'precision' in res.json['assets'][first_asset]


def test_control_delete(get_app):
    """Test /control/delete endpoint."""
    api = '/control/delete'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(api, headers=USER_HEADERS)
    assert res.status_code == 401

    asset_list = app.config['WALLET'].list_assets([])
    asset_id = asset_list.nia[0].asset_id

    # create a WAITING_COUNTERPARTY transfer + fail it
    _ = app.config['WALLET'].blind_receive(asset_id, None, 1,
                                           app.config["TRANSPORT_ENDPOINTS"],
                                           1)
    print('waiting for the transfer to expire...')
    time.sleep(2)
    resp = client.get(
        "/control/fail",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json['result'] is True
    asset_transfers = app.config['WALLET'].list_transfers(asset_id)
    transfers_failed = [
        t for t in asset_transfers if t.status == rgb_lib.TransferStatus.FAILED
    ]
    assert len(transfers_failed) == 1
    # delete the failed transfer
    resp = client.get(
        api,
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json['result'] is True
    asset_transfers = app.config['WALLET'].list_transfers(asset_id)
    transfers_failed = [
        t for t in asset_transfers if t.status == rgb_lib.TransferStatus.FAILED
    ]
    assert len(transfers_failed) == 0


def test_control_fail(get_app):
    """Test /control/fail endpoint."""
    api = '/control/fail'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(api, headers=USER_HEADERS)
    assert res.status_code == 401

    # return False is no transfer has changed
    resp = client.get(
        api,
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json['result'] is False

    asset_list = app.config['WALLET'].list_assets([])
    asset_id = asset_list.nia[0].asset_id

    # create a transfer in status WAITING_COUNTERPARTY with a 1s expiration
    _ = app.config['WALLET'].blind_receive(asset_id, None, 1,
                                           app.config["TRANSPORT_ENDPOINTS"],
                                           1)
    print('waiting for the transfer to expire...')
    time.sleep(2)
    asset_transfers = app.config['WALLET'].list_transfers(asset_id)
    transfers_wait_counterparty = [
        t for t in asset_transfers
        if t.status == rgb_lib.TransferStatus.WAITING_COUNTERPARTY
    ]
    assert len(transfers_wait_counterparty) == 1
    # fail the transfer
    resp = client.get(
        api,
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json['result'] is True
    asset_transfers = app.config['WALLET'].list_transfers(asset_id)
    transfers_wait_counterparty = [
        t for t in asset_transfers
        if t.status == rgb_lib.TransferStatus.WAITING_COUNTERPARTY
    ]
    assert not transfers_wait_counterparty
    transfers_failed = [
        t for t in asset_transfers if t.status == rgb_lib.TransferStatus.FAILED
    ]
    assert len(transfers_failed) == 1


def test_control_refresh(get_app):
    """Test /control/refresh/<asset_id> endpoint."""
    api = '/control/refresh'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(f"{api}/assetid", headers=USER_HEADERS)
    assert res.status_code == 401

    # bad asset ID
    resp = client.get(
        f"{api}/invalid",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 404
    assert 'Unknown asset ID' in resp.json['error']

    # transfer refresh
    user = prepare_user_wallets(app, 1)[0]
    check_receive_asset(app, user, None, 200)
    with app.app_context():
        requests = Request.query
        assert requests.count() == 1
        request = requests.one()
    asset_id = request.asset_id
    wait_scheduler_processing(app)
    resp = client.get(
        f"{api}/{asset_id}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json['result'] is False
    asset_transfers = app.config['WALLET'].list_transfers(asset_id)
    transfer = [t for t in asset_transfers if t.kind == TransferKind.SEND][0]
    assert transfer.status == rgb_lib.TransferStatus.WAITING_CONFIRMATIONS
    # mine a block + refresh the transfer + check it's now settled
    generate(1)
    resp = client.get(
        f"{api}/{asset_id}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json['result'] is True
    asset_transfers = app.config['WALLET'].list_transfers(asset_id)
    transfer = [t for t in asset_transfers if t.kind == TransferKind.SEND][0]
    assert transfer.status == rgb_lib.TransferStatus.SETTLED


def test_control_requests(get_app):  # pylint: disable=too-many-statements
    """Test /control/requests endpoint."""
    api = '/control/requests'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(api, headers=USER_HEADERS)
    assert res.status_code == 401

    # no requests
    resp = client.get(
        api,
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert not resp.json['requests']

    users = prepare_user_wallets(app, 2)
    scheduler.pause()
    add_fake_request(app,
                     users[0],
                     'group_1',
                     20,
                     amount=1,
                     asset_id=uuid.uuid4().hex,
                     hash_wallet_id=True)
    add_fake_request(app,
                     users[1],
                     'group_2',
                     20,
                     amount=2,
                     asset_id=uuid.uuid4().hex,
                     hash_wallet_id=True)
    add_fake_request(app,
                     users[0],
                     'group_1',
                     40,
                     amount=3,
                     asset_id=uuid.uuid4().hex)

    with app.app_context():
        all_reqs = Request.query.all()

    # requests in status 20 (default)
    resp_default = client.get(
        api,
        headers=OPERATOR_HEADERS,
    )
    assert resp_default.status_code == 200
    status = 20
    resp = client.get(
        f"{api}?status={status}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp_default.json == resp.json
    assert len(resp.json['requests']) == 2
    assert all(r.get('status') == status for r in resp.json['requests'])
    assert any(r.get('amount') == 1 for r in resp.json['requests'])
    assert any(r.get('amount') == 2 for r in resp.json['requests'])

    # filter by status
    status = 40
    resp = client.get(
        f"{api}?status={status}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json['requests']) == 1
    request = next(iter(resp.json['requests']))
    assert request['status'] == status
    assert request['amount'] == 3

    # filter by asset group
    asset_group = 'group_1'
    resp = client.get(
        f"{api}?asset_group={asset_group}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json['requests']) == 2
    assert all(
        r.get('asset_group') == asset_group for r in resp.json['requests'])

    # filter by asset ID
    req = random.choice(all_reqs)
    resp = client.get(
        f"{api}?asset_id={req.asset_id}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json['requests']) == 1
    assert all(
        r.get('asset_id') == req.asset_id for r in resp.json['requests'])
    assert all(r.get('amount') == req.amount for r in resp.json['requests'])

    # filter by blinded UTXO
    req = random.choice(all_reqs)
    resp = client.get(
        f"{api}?blinded_utxo={req.blinded_utxo}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json['requests']) == 1
    assert all(
        r.get('blinded_utxo') == req.blinded_utxo
        for r in resp.json['requests'])
    assert all(r.get('amount') == req.amount for r in resp.json['requests'])

    # filter by wallet ID
    req = random.choice(all_reqs)
    resp = client.get(
        f"{api}?wallet_id={req.wallet_id}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json['requests']) == 1
    assert all(
        r.get('wallet_id') == req.wallet_id for r in resp.json['requests'])
    assert all(r.get('amount') == req.amount for r in resp.json['requests'])

    # filter by asset group + status
    asset_group = 'group_1'
    status = 20
    resp = client.get(
        f"{api}?asset_group={asset_group}&status={status}",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json['requests']) == 1
    request = next(iter(resp.json['requests']))
    assert request['asset_group'] == asset_group
    assert request['status'] == status
    assert request['amount'] == 1


def test_control_transfers(get_app):  # pylint: disable=too-many-statements
    """Test /control/transfers endpoint."""
    api = '/control/transfers'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(api, headers=USER_HEADERS)
    assert res.status_code == 401

    # 0 pending (WAITING_COUNTERPARTY + WAITING_CONFIRMATIONS) transfers
    resp = client.get(
        api,
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert not resp.json['transfers']

    # 2 SETTLED transfer (issuances)
    resp = client.get(
        f"{api}?status=SETTLED",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json['transfers']) == 2
    for transfer in iter(resp.json['transfers']):
        assert transfer['kind'] == 'ISSUANCE'
        assert transfer['status'] == 'SETTLED'

    # 1 pending (WAITING_COUNTERPARTY + WAITING_CONFIRMATIONS) transfers
    user = prepare_user_wallets(app, 1)[0]
    check_receive_asset(app, user, None, 200)
    wait_scheduler_processing(app)
    resp = client.get(
        api,
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json['transfers']) == 1
    transfer = next(iter(resp.json['transfers']))
    assert 'amount' in transfer
    assert 'kind' in transfer
    assert transfer['kind'] == 'SEND'
    assert 'recipient_id' in transfer
    assert transfer['status'] == 'WAITING_CONFIRMATIONS'
    assert 'txid' in transfer
    assert len(transfer['transfer_transport_endpoints']) == 1
    tte = next(iter(transfer['transfer_transport_endpoints']))
    assert 'endpoint' in tte
    assert tte['transport_type'] == 'JSON_RPC'
    assert tte['used'] is True

    # 0 WAITING_COUNTERPARTY transfers
    resp = client.get(
        f"{api}?status=WAITING_COUNTERPARTY",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert not resp.json['transfers']

    # 0 FAILED transfers
    resp = client.get(
        f"{api}?status=FAILED",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert not resp.json['transfers']

    asset_list = app.config['WALLET'].list_assets([])
    asset_id = asset_list.nia[0].asset_id

    # 1 WAITING_COUNTERPARTY transfer
    _ = app.config['WALLET'].blind_receive(asset_id, None, 1,
                                           app.config["TRANSPORT_ENDPOINTS"],
                                           1)
    print('waiting for the transfer to expire...')
    time.sleep(2)
    resp = client.get(
        f"{api}?status=WAITING_COUNTERPARTY",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json['transfers']) == 1

    # 1 FAILED transfer
    resp = client.get(
        "/control/fail",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json['result'] is True
    resp = client.get(
        f"{api}?status=FAILED",
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert len(resp.json['transfers']) == 1


def test_control_unspents(get_app):
    """Test /control/unspents endpoint."""
    api = '/control/unspents'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(api, headers=USER_HEADERS)
    assert res.status_code == 401

    resp = client.get(
        api,
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    unspents = resp.json['unspents']
    assert len(unspents) == 6
    colorable = [u for u in unspents if u['utxo']['colorable']]
    vanilla = [u for u in unspents if not u['utxo']['colorable']]
    assert len(colorable) == 5
    assert len(vanilla) == 1
    for unspent in unspents:
        assert 'btc_amount' in unspent['utxo']
        assert 'colorable' in unspent['utxo']
        assert 'txid' in unspent['utxo']['outpoint']
        assert 'vout' in unspent['utxo']['outpoint']
        if unspent['rgb_allocations']:
            allocation = next(iter(unspent['rgb_allocations']))
            assert allocation['amount'] == ISSUE_AMOUNT
            assert 'asset_id' in allocation
            assert allocation['settled'] is True


def test_receive_asset(get_app):
    """Test /receive/asset/<wallet_id>/<blinded_utxo> endpoint."""
    api = '/receive/asset'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(f"{api}/wallet_id/blinded_utxo", headers=BAD_HEADERS)
    assert res.status_code == 401

    user = prepare_user_wallets(app, 1)[0]

    # check requests with xPub as wallet ID are denied
    wallet_id = user["xpub"]
    blinded_utxo = create_and_blind(app.config, user)
    resp = client.get(
        f"{api}/{wallet_id}/{blinded_utxo}",
        headers=USER_HEADERS,
    )
    assert resp.status_code == 403

    # standard request
    scheduler.pause()
    wallet_id = get_sha256_hex(user["xpub"])
    blinded_utxo = create_and_blind(app.config, user)
    resp = client.get(
        f"{api}/{wallet_id}/{blinded_utxo}",
        headers=USER_HEADERS,
    )
    assert resp.status_code == 200
    asset = resp.json["asset"]
    with app.app_context():
        requests = Request.query
        assert requests.count() == 1
        request = requests.one()
    assert request.status == 20
    asset_id = request.asset_id
    assert asset["asset_id"] == asset_id
    assert 'amount' in asset
    assert 'name' in asset
    assert 'precision' in asset
    assert 'schema' in asset

    scheduler.resume()
    wait_scheduler_processing(app)
    generate(1)
    wait_refresh(app.config['WALLET'], app.config['ONLINE'])
    with app.app_context():
        request = Request.query.filter_by(idx=request.idx).one()
    assert request.status == 40


def test_receive_config(get_app):
    """Test /receive/config/<wallet_id> endpoint."""
    api = '/receive/config'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(f"{api}/wallet_id", headers=BAD_HEADERS)
    assert res.status_code == 401

    bitcoin_network = getattr(rgb_lib.BitcoinNetwork, "REGTEST")
    xpub = rgb_lib.generate_keys(bitcoin_network).xpub
    check_requests_left(app, xpub, {"group_1": 1})


def test_reserve_topupbtc(get_app):
    """Test /reserve/top_up_btc endpoint."""
    api = '/reserve/top_up_btc'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(api, headers=BAD_HEADERS)
    assert res.status_code == 401

    wallet = app.config['WALLET']
    user = prepare_user_wallets(app, 1)[0]

    # get an address
    resp = client.get(
        api,
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    address = resp.json['address']

    # send some BTC + check the updated balance
    amount = 1000
    balance_1 = wallet.get_btc_balance(app.config['ONLINE']).vanilla
    txid = user['wallet'].send_btc(user['online'], address, amount,
                                   app.config['FEE_RATE'])
    assert txid
    balance_2 = wallet.get_btc_balance(app.config['ONLINE']).vanilla
    assert balance_2.settled == balance_1.settled
    assert balance_2.future == balance_1.future + amount
    assert balance_2.spendable == balance_1.spendable + amount

    # check settled balance updates after the tx gets confirmed
    generate(1)
    balance_3 = wallet.get_btc_balance(app.config['ONLINE']).vanilla
    assert balance_3.settled == balance_1.settled + amount


def test_reserve_topuprgb(get_app):  # pylint: disable=too-many-locals
    """Test /reserve/top_up_rgb endpoint."""
    api = '/reserve/top_up_rgb'
    app = get_app()
    client = app.test_client()

    # auth failure
    res = client.get(api, headers=BAD_HEADERS)
    assert res.status_code == 401

    wallet = app.config['WALLET']
    user = prepare_user_wallets(app, 1)[0]

    # send some assets from the faucet to the user wallet
    wallet_id = get_sha256_hex(user["xpub"])
    blinded_utxo = create_and_blind(app.config, user)
    resp = client.get(
        f"/receive/asset/{wallet_id}/{blinded_utxo}",
        headers=USER_HEADERS,
    )
    assert resp.status_code == 200
    asset = resp.json["asset"]
    asset_id = asset['asset_id']
    wait_scheduler_processing(app)
    wait_refresh(user['wallet'], user['online'])
    generate(1)
    wait_refresh(user['wallet'], user['online'])
    wait_refresh(wallet, app.config['ONLINE'])
    balance_1 = wallet.get_asset_balance(asset_id)

    # send some assets from the user to the faucet wallet
    resp = client.get(
        api,
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert 'expiration' in resp.json
    blinded_utxo = resp.json['blinded_utxo']
    amount = 1
    recipient_map = {
        asset_id: [
            rgb_lib.Recipient(blinded_utxo, None, amount,
                              app.config['TRANSPORT_ENDPOINTS']),
        ]
    }
    created = user['wallet'].create_utxos(user['online'], True, 1, None,
                                          app.config['FEE_RATE'])
    assert created == 1
    txid = user['wallet'].send(user['online'], recipient_map, True,
                               app.config['FEE_RATE'], 1)
    assert txid
    wait_refresh(wallet, app.config['ONLINE'])
    # check future balance updates once the transfer is in WAITING_CONFIRMATIONS
    balance_2 = wallet.get_asset_balance(asset_id)
    assert balance_2.settled == balance_1.settled
    assert balance_2.future == balance_1.future + amount
    assert balance_2.spendable == balance_1.spendable
    # check settled + spendable balances update after the tx gets confirmed
    generate(1)
    wait_refresh(wallet, app.config['ONLINE'])
    wait_refresh(user['wallet'], user['online'])
    balance_3 = wallet.get_asset_balance(asset_id)
    assert balance_3.settled == balance_1.settled + amount
    assert balance_3.spendable == balance_1.spendable + amount