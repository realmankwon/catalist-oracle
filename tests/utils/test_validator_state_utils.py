import pytest

from src.constants import FAR_FUTURE_EPOCH
from src.providers.consensus.typings import Validator, ValidatorStatus, ValidatorState
from src.typings import EpochNumber, Gwei
from src.utils.validator_state import (
    calculate_total_active_effective_balance,
    is_on_exit,
    get_validator_age,
    calculate_active_effective_balance_sum,
    is_validator_eligible_to_exit,
    is_fully_withdrawable_validator,
    has_eth1_withdrawal_credential,
    is_exited_validator,
    is_active_validator
)
from tests.factory.no_registry import ValidatorFactory
from tests.modules.accounting.bunker.test_bunker_abnormal_cl_rebase import simple_validators


@pytest.mark.unit
@pytest.mark.parametrize(
    ("validators", "expected_balance"),
    [
        ([], 1 * 10 ** 9),
        (
            [Validator('0', '1', ValidatorStatus.ACTIVE_ONGOING,
                              ValidatorState('0x0', '', str(32 * 10 ** 9), False, '', '15000', '15001', '')),
             Validator('1', '1', ValidatorStatus.ACTIVE_EXITING,
                              ValidatorState('0x1', '', str(31 * 10 ** 9), False, '', '14999', '15000', '')),
             Validator('2', '1', ValidatorStatus.ACTIVE_SLASHED,
                              ValidatorState('0x2', '', str(31 * 10 ** 9), True, '', '15000', '15001', ''))],
            63 * 10 ** 9,
        ),
        (
            [
                Validator('0', '1', ValidatorStatus.ACTIVE_ONGOING,
                              ValidatorState('0x0', '', str(32 * 10 ** 9), False, '', '14000', '14999', '')),
                Validator('1', '1', ValidatorStatus.EXITED_SLASHED,
                              ValidatorState('0x1', '', str(32 * 10 ** 9), True, '', '15000', '15000', ''))
            ],
            1 * 10 ** 9,
        ),
    ]
)
def test_calculate_total_active_effective_balance(validators, expected_balance):
    total_effective_balance = calculate_total_active_effective_balance(validators, EpochNumber(15000))
    assert total_effective_balance == expected_balance

@pytest.mark.unit
@pytest.mark.parametrize(
    ("validators", "expected_balance"),
    [
        ([], 0),
        (
            [Validator('0', '1', ValidatorStatus.ACTIVE_ONGOING,
                              ValidatorState('0x0', '', str(32 * 10 ** 9), False, '', '15000', '15001', '')),
             Validator('1', '1', ValidatorStatus.ACTIVE_EXITING,
                              ValidatorState('0x1', '', str(31 * 10 ** 9), False, '', '14999', '15000', '')),
             Validator('2', '1', ValidatorStatus.ACTIVE_SLASHED,
                              ValidatorState('0x2', '', str(31 * 10 ** 9), True, '', '15000', '15001', ''))],
            63 * 10 ** 9,
        ),
        (
            [
                Validator('0', '1', ValidatorStatus.ACTIVE_ONGOING,
                              ValidatorState('0x0', '', str(32 * 10 ** 9), False, '', '14000', '14999', '')),
                Validator('1', '1', ValidatorStatus.EXITED_SLASHED,
                              ValidatorState('0x1', '', str(32 * 10 ** 9), True, '', '15000', '15000', ''))
            ],
            0,
        ),
    ]
)
def test_calculate_active_effective_balance_sum(validators, expected_balance):
    total_effective_balance = calculate_active_effective_balance_sum(validators, EpochNumber(15000))
    assert total_effective_balance == expected_balance


@pytest.mark.unit
@pytest.mark.parametrize(
    ('exit_epoch', 'expected'),
    [(100500, True),
     (FAR_FUTURE_EPOCH, False)]
)
def test_is_on_exit(exit_epoch, expected):
    validator = object.__new__(Validator)
    validator.validator = object.__new__(ValidatorState)
    validator.validator.exit_epoch = exit_epoch
    assert is_on_exit(validator) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ('validator_activation_epoch', 'ref_epoch', 'expected_result'),
    [
        (100, 100, 0),
        (100, 101, 1),
        (100, 99, 0),
    ]
)
def test_get_validator_age(validator_activation_epoch, ref_epoch, expected_result):
    validator = object.__new__(Validator)
    validator.validator = object.__new__(ValidatorState)
    validator.validator.activation_epoch = validator_activation_epoch
    assert get_validator_age(validator, ref_epoch) == expected_result

@pytest.mark.unit
@pytest.mark.parametrize("activation_epoch, epoch, exit_epoch, expected", [
    (176720, 176720, 176722, True),
    (176720, 176721, 176722, True),
    (176900, 176900, 2 ** 64 - 1, True),
    (176901, 176900, 2 ** 64 - 1, False),
    (176720, 176720, 176720, False),
    (176900, 176720, 176720, False),
    (176900, 176720, 176750, False),
])
def test_is_active_validator(activation_epoch, epoch, exit_epoch, expected):
    validator = ValidatorFactory.build()
    validator.validator.activation_epoch = activation_epoch
    validator.validator.exit_epoch = exit_epoch

    actual = is_active_validator(validator, EpochNumber(epoch))
    assert actual == expected


@pytest.mark.unit
@pytest.mark.parametrize("exit_epoch, epoch, expected", [
    (176720, 176722, True),
    (176730, 176722, False),
    (2 ** 64 - 1, 176722, False),
])
def test_is_exited_validator(exit_epoch, epoch, expected):
    validator = ValidatorFactory.build()
    validator.validator.exit_epoch = exit_epoch

    actual = is_exited_validator(validator, EpochNumber(epoch))
    assert actual == expected


@pytest.mark.unit
@pytest.mark.parametrize("exit_epoch, expected", [
    (176720, True),
    (2 ** 64 - 1, False),
])
def test_is_on_exit(exit_epoch, expected):
    validator = ValidatorFactory.build()
    validator.validator.exit_epoch = exit_epoch

    actual = is_on_exit(validator)
    assert actual == expected


@pytest.mark.unit
@pytest.mark.parametrize("withdrawal_credentials, expected", [
    ('0x01ba', True),
    ('01ab', False),
    ('0x00ba', False),
    ('00ba', False),
])
def test_has_eth1_withdrawal_credential(spec, withdrawal_credentials, expected):
    validator = ValidatorFactory.build()
    validator.validator.withdrawal_credentials = withdrawal_credentials

    actual = has_eth1_withdrawal_credential(spec, validator)
    assert actual == expected


@pytest.mark.unit
@pytest.mark.parametrize("withdrawable_epoch, balance, epoch, expected", [
    (176720, 32 * (10 ** 10), 176722, True),
    (176722, 32 * (10 ** 10), 176722, True),
    (176723, 32 * (10 ** 10), 176722, False),
    (176722, 0, 176722, False),
])
def test_is_fully_withdrawable_validator(spec, withdrawable_epoch, balance, epoch, expected):
    validator = ValidatorFactory.build()
    validator.validator.withdrawable_epoch = withdrawable_epoch
    validator.validator.withdrawal_credentials = '0x01ba'
    validator.balance = balance

    actual = is_fully_withdrawable_validator(spec, validator, EpochNumber(epoch))
    assert actual == expected


@pytest.mark.unit
@pytest.mark.parametrize("activation_epoch, exit_epoch, epoch, expected", [
    (170000, 2 ** 64 - 1, 170256, True),
    (170000, 170200, 170256, False),
    (170000, 2 ** 64 - 1, 170255, False),
])
def test_is_validator_eligible_to_exit(spec, activation_epoch, exit_epoch, epoch, expected):
    validator = ValidatorFactory.build()
    validator.validator.activation_epoch = activation_epoch
    validator.validator.exit_epoch = exit_epoch

    actual = is_validator_eligible_to_exit(spec, validator, EpochNumber(epoch))
    assert actual == expected


def get_validators():
    validators = ValidatorFactory.batch(2)

    validators[0].validator.activation_epoch = 170000
    validators[0].validator.exit_epoch = 2 ** 64 - 1
    validators[0].validator.effective_balance = 1000000000
    validators[0].validator.withdrawal_credentials = '0x01ba'

    validators[1].validator.activation_epoch = 170001
    validators[1].validator.exit_epoch = 2 ** 64 - 1
    validators[1].validator.effective_balance = 2000000000
    validators[1].validator.withdrawal_credentials = '0x01ba'

    return validators


@pytest.mark.unit
def test_calculate_total_active_effective_balance(spec):
    actual = calculate_total_active_effective_balance(spec, get_validators(), EpochNumber(170256))
    assert actual == Gwei(3000000000)

    actual = calculate_total_active_effective_balance(spec, simple_validators(0, 9, effective_balance=0), EpochNumber(170256))
    assert actual == int(spec.EFFECTIVE_BALANCE_INCREMENT)

    vals = get_validators()
    vals[0].validator.exit_epoch = 170000  # non active validator

    actual = calculate_total_active_effective_balance(spec, vals, EpochNumber(170256))
    assert actual == Gwei(2000000000)
