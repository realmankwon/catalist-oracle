import pytest
from _pytest._io import TerminalWriter
from web3_multi_provider import MultiProvider
from xdist import is_xdist_controller
from xdist.dsession import TerminalDistReporter, DSession

from src import variables
from src.typings import EpochNumber, SlotNumber, BlockRoot
from src.utils.blockstamp import build_blockstamp
from src.utils.slot import get_reference_blockstamp
from src.web3py.contract_tweak import tweak_w3_contracts
from src.web3py.extensions import (
    ConsensusClientModule, KeysAPIClientModule, LidoValidatorsProvider, TransactionUtils,
    LidoContracts,
)
from src.web3py.typings import Web3


@pytest.fixture()
def web3():
    web3 = Web3(MultiProvider(variables.EXECUTION_CLIENT_URI))
    tweak_w3_contracts(web3)
    cc = ConsensusClientModule(variables.CONSENSUS_CLIENT_URI, web3)
    kac = KeysAPIClientModule(variables.KEYS_API_URI, web3)

    web3.attach_modules({
        'lido_validators': LidoValidatorsProvider,
        'transaction': TransactionUtils,
        'cc': lambda: cc,  # type: ignore[dict-item]
        'kac': lambda: kac,  # type: ignore[dict-item]
    })
    if variables.LIDO_LOCATOR_ADDRESS:
        web3.attach_modules({'lido_contracts': LidoContracts})

    return web3


@pytest.fixture(params=[pytest.param("finalized_blockstamp", id="Finalized blockstamp"),
                        pytest.param("blockstamp_frame_ago", id="Blockstamp frame ago")])
def blockstamp(request):
    return request.getfixturevalue(request.param)


@pytest.fixture
def finalized_blockstamp(web3):
    block_root = BlockRoot(web3.cc.get_block_root('finalized').root)
    block_details = web3.cc.get_block_details(block_root)
    bs = build_blockstamp(block_details)
    cc_config = web3.cc.get_config_spec()
    return get_reference_blockstamp(
        web3.cc,
        bs.slot_number,
        ref_epoch=EpochNumber(bs.slot_number // int(cc_config.SLOTS_PER_EPOCH)),
        last_finalized_slot_number=bs.slot_number
    )


@pytest.fixture
def blockstamp_frame_ago(web3, finalized_blockstamp):
    epochs_per_frame = 270
    cc_config = web3.cc.get_config_spec()
    slots_per_frame = epochs_per_frame * int(cc_config.SLOTS_PER_EPOCH)
    last_report_ref_slot = SlotNumber(finalized_blockstamp.slot_number - slots_per_frame)

    return get_reference_blockstamp(
        web3.cc,
        last_report_ref_slot,
        ref_epoch=EpochNumber(last_report_ref_slot // int(cc_config.SLOTS_PER_EPOCH)),
        last_finalized_slot_number=finalized_blockstamp.slot_number
    )


def pytest_collection_modifyitems(items):
    """Sort tests by finalized blockstamp first."""
    items.sort(key=lambda x: "Finalized blockstamp" in x.nodeid, reverse=True)


class CustomTerminal(TerminalDistReporter):

    def ensure_show_status(self):
        pass

    def pytest_xdist_newgateway(self, gateway):
        self.setstatus(gateway.spec, "C")

    def pytest_testnodeready(self, node):
        self.setstatus(node.gateway.spec, "ok")


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    class SessionLike:
        config = None
    session_like = SessionLike()
    session_like.config = config
    if is_xdist_controller(session_like):
        dsession = config.pluginmanager.getplugin("dsession")
        config.pluginmanager.unregister(dsession.trdist, "terminaldistreporter")

        custom_terminal = CustomTerminal(config)
        dsession.trdist = custom_terminal
        config.pluginmanager.register(custom_terminal)


def pytest_report_teststatus(report, config):
    if report.when == "call":
        if report.passed:
            return "passed", "✅ Checked", "✅ Checked"
        if report.failed:
            return "failed", "❌ Failed", "❌ Failed"
        if report.skipped:
            return "skipped", "Skipped", "Skipped"
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report) -> None:
    if report.when == 'call':
        print(report.user_properties[0], end="")
    if report.when == 'teardown':
        print()


def pytest_runtest_call(item):
    tw: TerminalWriter = item.config.pluginmanager.get_plugin("terminalreporter")._tw  # pylint: disable=protected-access

    module_doc = item.parent.obj.__doc__
    if not module_doc:
        module_doc = f"Placeholder doc for {item.parent.obj.__name__}"

    check_doc = item.obj.__doc__
    if not check_doc:
        check_doc = f"Placeholder doc for {item.obj.__name__}"

    check_params = f"[{item.callspec.id}]" if hasattr(item, "callspec") else ""

    check_params_colorized = tw.markup(check_params, cyan=True)
    module_doc_colorized = tw.markup(f"[{module_doc}]", blue=True)
    message = f"{module_doc_colorized}{check_params_colorized} {check_doc}"
    item.user_properties.append(f">> {message}... ")
