from unittest.mock import Mock

import pytest

from src.modules.submodules.typings import ChainConfig
from src.providers.consensus.typings import ValidatorState, Validator, ValidatorStatus
from src.services.exit_order_iterator import NodeOperatorPredictableState
from src.services.exit_order_iterator_state import ExitOrderIteratorStateService
from src.web3py.extensions.catalist_validators import (
    NodeOperator,
    StakingModule,
)
from tests.factory.blockstamp import ReferenceBlockStampFactory


FAR_FUTURE_EPOCH = 2**64 - 1
TESTING_VALIDATOR_DELAYED_TIMEOUT_IN_SLOTS = 10


def simple_validators(
    from_index: int, to_index: int, slashed=False, activation_epoch=0, exit_epoch=FAR_FUTURE_EPOCH
) -> list[Validator]:
    validators = []
    for index in range(from_index, to_index + 1):
        validator = Validator(
            index=str(index),
            balance=str(32 * 10**9),
            status=ValidatorStatus.ACTIVE_ONGOING,
            validator=ValidatorState(
                pubkey=f"0x{index}",
                withdrawal_credentials='',
                effective_balance=str(32 * 10**9),
                slashed=slashed,
                activation_eligibility_epoch='',
                activation_epoch=str(activation_epoch),
                exit_epoch=exit_epoch,
                withdrawable_epoch=exit_epoch,
            ),
        )
        validators.append(validator)
    return validators


def simple_operator(
    module_id,
    index,
    is_limited,
    target_count,
    refunded_count,
    total_deposited,
):
    operator = object.__new__(NodeOperator)
    operator.staking_module = object.__new__(StakingModule)
    operator.id = index
    operator.is_target_limit_active = is_limited
    operator.target_validators_count = target_count
    operator.refunded_validators_count = refunded_count
    operator.total_deposited_validators = total_deposited
    operator.staking_module.id = module_id
    operator.staking_module.staking_module_address = f'0x{module_id}'
    return operator


@pytest.fixture()
def past_blockstamp():
    yield ReferenceBlockStampFactory.build(ref_epoch=4445)


@pytest.fixture
def mock_get_validators(exit_order_state):
    def _get_validators(blockstamp):
        responses = {
            100: simple_validators(0, 19),
            200: [
                *simple_validators(0, 9, exit_epoch=100500),
                *simple_validators(10, 19),
            ],
        }

        return responses[blockstamp.slot_number]

    exit_order_state.w3.cc.get_validators = Mock(side_effect=_get_validators)


@pytest.fixture
def mock_get_catalist_validators(exit_order_state):
    def _get_catalist_validators(blockstamp):
        responses = {
            100: simple_validators(0, 9),
            200: [
                simple_validators(0, 9, exit_epoch=100500)[-1],
                simple_validators(10, 19)[-1],
            ],
        }
        return responses[blockstamp.slot_number]

    exit_order_state.w3.catalist_validators.get_catalist_validators = Mock(side_effect=_get_catalist_validators)


@pytest.fixture
def mock_get_recently_requests_to_exit_indexes(exit_order_state):
    def _get_recently_requests_to_exit_indexes(blockstamp, *_):
        responses = {
            100: {
                (0, 0): [8, 9],
                (0, 1): [11],
                (1, 1): [24, 25],
                (1, 2): [],
            },
            200: {
                (0, 0): [],
                (0, 1): [],
                (1, 1): [],
                (1, 2): [],
            },
        }

        return responses[blockstamp.slot_number]

    exit_order_state.get_recently_requests_to_exit_indexes_by_operators = Mock(
        side_effect=_get_recently_requests_to_exit_indexes
    )


@pytest.fixture
def mock_get_oracle_daemon_config_get(exit_order_state, contracts):
    def _get_oracle_daemon_config_get(key):
        def _inner_call(block_identifier):
            responses = {
                'NODE_OPERATOR_NETWORK_PENETRATION_THRESHOLD_BP': 1000,
            }

            return responses[key]

        func = lambda _: None
        func.call = _inner_call

        return func

    exit_order_state.w3.catalist_contracts.oracle_daemon_config.functions.get = Mock(
        side_effect=_get_oracle_daemon_config_get
    )


@pytest.fixture
def exit_order_state(
    web3,
    catalist_validators,
    past_blockstamp,
) -> ExitOrderIteratorStateService:
    """Returns minimal initialized ValidatorsExit service instance"""
    service = object.__new__(ExitOrderIteratorStateService)
    service.w3 = web3
    service.blockstamp = past_blockstamp
    return service


@pytest.mark.unit
def test_get_exitable_catalist_validators(
    exit_order_state,
):
    exit_order_state._operator_validators = {
        (0, 0): simple_validators(0, 9),
        (0, 1): simple_validators(10, 19),
        (1, 1): [
            *simple_validators(20, 24),
            *simple_validators(25, 29, exit_epoch=100500),
        ],
        (1, 2): simple_validators(30, 39, activation_epoch=1),
    }
    exit_order_state._operator_last_requested_to_exit_indexes = {
        (0, 0): 9,
        (0, 1): 11,
        (1, 1): 25,
        (1, 2): -1,
    }

    result = exit_order_state.get_exitable_catalist_validators()

    assert result == [
        *simple_validators(12, 19),
        *simple_validators(30, 39, activation_epoch=1),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    'blockstamp, expected_result',
    [
        (
            ReferenceBlockStampFactory.build(slot_number=100),
            {
                (0, 0): NodeOperatorPredictableState(
                    predictable_validators_total_age=0,
                    predictable_validators_count=0,
                    targeted_validators_limit_is_enabled=False,
                    targeted_validators_limit_count=0,
                    delayed_validators_count=6,
                ),
                (0, 1): NodeOperatorPredictableState(
                    predictable_validators_total_age=73560,
                    predictable_validators_count=8,
                    targeted_validators_limit_is_enabled=False,
                    targeted_validators_limit_count=0,
                    delayed_validators_count=0,
                ),
                (1, 1): NodeOperatorPredictableState(
                    predictable_validators_total_age=0,
                    predictable_validators_count=0,
                    targeted_validators_limit_is_enabled=True,
                    targeted_validators_limit_count=5,
                    delayed_validators_count=4,
                ),
                (1, 2): NodeOperatorPredictableState(
                    predictable_validators_total_age=91940,
                    predictable_validators_count=10,
                    targeted_validators_limit_is_enabled=True,
                    targeted_validators_limit_count=0,
                    delayed_validators_count=0,
                ),
            },
        ),
        (
            ReferenceBlockStampFactory.build(slot_number=200),
            {
                (0, 0): NodeOperatorPredictableState(
                    predictable_validators_total_age=0,
                    predictable_validators_count=0,
                    targeted_validators_limit_is_enabled=False,
                    targeted_validators_limit_count=0,
                    delayed_validators_count=8,
                ),
                (0, 1): NodeOperatorPredictableState(
                    predictable_validators_total_age=73560,
                    predictable_validators_count=8,
                    targeted_validators_limit_is_enabled=False,
                    targeted_validators_limit_count=0,
                    delayed_validators_count=1,
                ),
                (1, 1): NodeOperatorPredictableState(
                    predictable_validators_total_age=0,
                    predictable_validators_count=0,
                    targeted_validators_limit_is_enabled=True,
                    targeted_validators_limit_count=5,
                    delayed_validators_count=5,
                ),
                (1, 2): NodeOperatorPredictableState(
                    predictable_validators_total_age=91940,
                    predictable_validators_count=10,
                    targeted_validators_limit_is_enabled=True,
                    targeted_validators_limit_count=0,
                    delayed_validators_count=0,
                ),
            },
        ),
    ],
)
def test_prepare_catalist_node_operator_stats(
    exit_order_state,
    mock_get_recently_requests_to_exit_indexes,
    blockstamp,
    expected_result,
):
    chain_config = ChainConfig(slots_per_epoch=32, seconds_per_slot=12, genesis_time=0)

    exit_order_state._operators = [
        simple_operator(0, 0, False, 0, 2, 10),
        simple_operator(0, 1, False, 0, 1, 10),
        simple_operator(1, 1, True, 5, 0, 10),
        simple_operator(1, 2, True, 0, 5, 10),
    ]
    exit_order_state._operator_validators = {
        (0, 0): simple_validators(0, 9),
        (0, 1): simple_validators(10, 19),
        (1, 1): [
            *simple_validators(20, 24),
            *simple_validators(25, 29, exit_epoch=100500),
        ],
        (1, 2): simple_validators(30, 39, activation_epoch=1),
    }
    exit_order_state._operator_last_requested_to_exit_indexes = {
        (0, 0): 9,
        (0, 1): 11,
        (1, 1): 25,
        (1, 2): -1,
    }

    result = exit_order_state.prepare_catalist_node_operator_stats(blockstamp, chain_config)

    assert result == expected_result


@pytest.mark.unit
@pytest.mark.parametrize(
    'blockstamp, catalist_node_operators_stats, expected_result',
    [
        (ReferenceBlockStampFactory.build(slot_number=100), {}, 10),
        (
            ReferenceBlockStampFactory.build(slot_number=100),
            {(0, 1): NodeOperatorPredictableState(0, 10, False, 0, 0)},
            20,
        ),
        (
            ReferenceBlockStampFactory.build(slot_number=200),
            {
                (0, 1): NodeOperatorPredictableState(0, 10, False, 0, 0),
                (1, 1): NodeOperatorPredictableState(0, 10, False, 0, 0),
            },
            29,
        ),
    ],
)
def test_get_total_predictable_validators_count(
    exit_order_state,
    mock_get_validators,
    mock_get_catalist_validators,
    catalist_node_operators_stats,
    blockstamp,
    expected_result,
):
    result = exit_order_state.get_total_predictable_validators_count(blockstamp, catalist_node_operators_stats)

    assert result == expected_result


@pytest.mark.unit
@pytest.mark.parametrize(
    'blockstamp, operator_validators, last_requested_to_exit_index, expected_result',
    [
        (ReferenceBlockStampFactory.build(ref_epoch=100), simple_validators(0, 9), 0, (900, 9)),
        (ReferenceBlockStampFactory.build(ref_epoch=100), simple_validators(0, 9), 1, (800, 8)),
        (ReferenceBlockStampFactory.build(ref_epoch=100), simple_validators(0, 9, activation_epoch=10), 5, (360, 4)),
    ],
)
def test_count_operator_validators_stats(
    blockstamp, operator_validators, last_requested_to_exit_index, expected_result
):
    result = ExitOrderIteratorStateService.count_operator_validators_stats(
        blockstamp,
        operator_validators,
        last_requested_to_exit_index,
    )

    assert result == expected_result


@pytest.mark.unit
@pytest.mark.parametrize(
    'operator_validators, recently_operator_requested_to_exit_index, last_requested_to_exit_index, expected_result',
    [
        ([], {}, 0, 0),
        (simple_validators(0, 9, exit_epoch="100500"), {7, 8, 9}, 9, 0),
        (simple_validators(0, 9), {}, 0, 1),
        (simple_validators(0, 9), {}, 1, 2),
        (simple_validators(0, 9), {1}, 1, 1),
        (simple_validators(0, 9), {7, 8}, 8, 7),
    ],
)
def test_count_operator_delayed_validators(
    operator_validators,
    recently_operator_requested_to_exit_index,
    last_requested_to_exit_index,
    expected_result,
):
    result = ExitOrderIteratorStateService.count_operator_delayed_validators(
        operator_validators,
        recently_operator_requested_to_exit_index,
        last_requested_to_exit_index,
    )
    assert result == expected_result


@pytest.mark.unit
def test_get_operator_network_penetration_threshold(
    exit_order_state, mock_get_oracle_daemon_config_get, past_blockstamp
):
    assert exit_order_state.get_operator_network_penetration_threshold(past_blockstamp) == 0.1


@pytest.mark.unit
def test_exit_order_iterator_state_service_init(web3, past_blockstamp, catalist_validators, contracts):
    web3.catalist_validators.get_catalist_node_operators = lambda _: []
    web3.catalist_validators.get_catalist_validators_by_node_operators = lambda _: []
    ExitOrderIteratorStateService.get_operators_with_last_exited_validator_indexes = lambda _, __: {}
    exit_order_iterator_state_service = ExitOrderIteratorStateService(web3, past_blockstamp)
    assert exit_order_iterator_state_service is not None
