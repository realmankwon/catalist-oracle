import logging
from copy import deepcopy
from functools import reduce
from typing import Sequence, Iterable

from eth_typing import HexStr

from src.constants import FAR_FUTURE_EPOCH, SHARD_COMMITTEE_PERIOD
from src.metrics.prometheus.accounting import (
    ACCOUNTING_STUCK_VALIDATORS,
    ACCOUNTING_EXITED_VALIDATORS,
    ACCOUNTING_DELAYED_VALIDATORS,
)
from src.modules.accounting.extra_data import ExtraDataService, ExtraData
from src.modules.accounting.typings import OracleReportLimits
from src.modules.submodules.typings import ChainConfig
from src.typings import BlockStamp, ReferenceBlockStamp, EpochNumber
from src.utils.abi import named_tuple_to_dataclass
from src.utils.events import get_events_in_past
from src.utils.types import bytes_to_hex_str
from src.utils.validator_state import is_exited_validator, is_validator_eligible_to_exit, is_on_exit
from src.utils.cache import global_lru_cache as lru_cache
from src.web3py.extensions.catalist_validators import (
    NodeOperatorGlobalIndex,
    CatalistValidator,
    StakingModule,
)
from src.web3py.typings import Web3


logger = logging.getLogger(__name__)


class CatalistValidatorStateService:
    """Helper that calculates/aggregates Catalist validator's states."""
    def __init__(self, w3: Web3):
        self.w3 = w3
        self.extra_data_service = ExtraDataService()

    @lru_cache(maxsize=1)
    def get_extra_data(self, blockstamp: ReferenceBlockStamp, chain_config: ChainConfig) -> ExtraData:
        stuck_validators = self.get_catalist_newly_stuck_validators(blockstamp, chain_config)
        logger.info({'msg': 'Calculate stuck validators.', 'value': stuck_validators})
        exited_validators = self.get_catalist_newly_exited_validators(blockstamp)
        logger.info({'msg': 'Calculate exited validators.', 'value': exited_validators})
        orl = self.get_oracle_report_limits(blockstamp)

        extra_data = self.extra_data_service.collect(
            stuck_validators=stuck_validators,
            exited_validators=exited_validators,
            max_items_count=orl.max_accounting_extra_data_list_items_count,
            max_no_in_payload_count=orl.max_node_operators_per_extra_data_item_count,
        )
        logger.info({'msg': 'Calculate extra data.', 'value': extra_data})
        return extra_data

    def get_catalist_newly_stuck_validators(self, blockstamp: ReferenceBlockStamp, chain_config: ChainConfig) -> dict[NodeOperatorGlobalIndex, int]:
        catalist_validators_by_no = self.w3.catalist_validators.get_catalist_validators_by_node_operators(blockstamp)
        ejected_index = self.get_operators_with_last_exited_validator_indexes(blockstamp)
        recently_requested_to_exit_pubkeys = self.get_last_requested_to_exit_pubkeys(blockstamp, chain_config)

        result = {}

        for global_no_index, validators in catalist_validators_by_no.items():
            def sum_stuck_validators(total: int, validator: CatalistValidator) -> int:
                # If validator index is higher than ejected index - we didn't request this validator to exit
                if int(validator.index) > ejected_index[global_no_index]:
                    return total

                # If validator don't have FAR_FUTURE_EPOCH, then it's already going to exit
                if int(validator.validator.exit_epoch) != FAR_FUTURE_EPOCH:
                    return total

                # If validator's pub key in recent events, node operator has still time to eject these validators
                if validator.catalist_id.key in recently_requested_to_exit_pubkeys:
                    return total

                validator_available_to_exit_epoch = int(validator.validator.activation_epoch) + SHARD_COMMITTEE_PERIOD
                delinquent_timeout_in_slots = self.get_validator_delinquent_timeout_in_slot(blockstamp)

                last_slot_to_exit = validator_available_to_exit_epoch * chain_config.slots_per_epoch + delinquent_timeout_in_slots

                if blockstamp.ref_slot <= last_slot_to_exit:
                    return total

                return total + 1

            result[global_no_index] = reduce(
                sum_stuck_validators,
                validators,
                0,
            )

        # Find only updated states for Node Operator
        node_operators = self.w3.catalist_validators.get_catalist_node_operators(blockstamp)

        for operator in node_operators:
            global_index = (operator.staking_module.id, operator.id)
            ACCOUNTING_STUCK_VALIDATORS.labels(*global_index).set(result[global_index])
            # If amount of stuck validators weren't changed skip report for operator
            if result[global_index] == operator.stuck_validators_count:
                del result[global_index]

        return result

    def get_last_requested_to_exit_pubkeys(self, blockstamp: ReferenceBlockStamp, chain_config: ChainConfig) -> set[HexStr]:
        exiting_keys_stuck_border_in_slots = self.get_validator_delinquent_timeout_in_slot(blockstamp)

        events = get_events_in_past(
            self.w3.catalist_contracts.validators_exit_bus_oracle.events.ValidatorExitRequest,  # type: ignore[arg-type]
            to_blockstamp=blockstamp,
            for_slots=exiting_keys_stuck_border_in_slots,
            seconds_per_slot=chain_config.seconds_per_slot,
        )

        logger.info({'msg': f'Fetch exit events. Got {len(events)} events.'})

        return set(bytes_to_hex_str(event['args']['validatorPubkey']) for event in events)

    @lru_cache(maxsize=1)
    def get_validator_delinquent_timeout_in_slot(self, blockstamp: ReferenceBlockStamp) -> int:
        exiting_keys_stuck_border_in_slots_bytes = self.w3.catalist_contracts.oracle_daemon_config.functions.get(
            'VALIDATOR_DELINQUENT_TIMEOUT_IN_SLOTS'
        ).call(block_identifier=blockstamp.block_hash)

        return self.w3.to_int(exiting_keys_stuck_border_in_slots_bytes)

    def get_operators_with_last_exited_validator_indexes(self, blockstamp: BlockStamp) -> dict[NodeOperatorGlobalIndex, int]:
        node_operators = self.w3.catalist_validators.get_catalist_node_operators(blockstamp)
        staking_modules = self.w3.catalist_validators.get_staking_modules(blockstamp)

        result = {}

        for module in staking_modules:
            node_operators_ids_in_module = list(map(lambda op: op.id, filter(lambda operator: operator.staking_module.id == module.id, node_operators)))

            last_requested_validators = self._get_last_requested_validator_indices(blockstamp, module, node_operators_ids_in_module)

            for no_id, validator_index in zip(node_operators_ids_in_module, last_requested_validators):
                result[(module.id, no_id)] = validator_index

        return result

    @lru_cache(maxsize=1)
    def get_catalist_newly_exited_validators(self, blockstamp: ReferenceBlockStamp) -> dict[NodeOperatorGlobalIndex, int]:
        catalist_validators = deepcopy(self.get_exited_catalist_validators(blockstamp))
        node_operators = self.w3.catalist_validators.get_catalist_node_operators(blockstamp)

        for operator in node_operators:
            global_index = (operator.staking_module.id, operator.id)
            ACCOUNTING_EXITED_VALIDATORS.labels(*global_index).set(catalist_validators[global_index])
            # If amount of exited validators weren't changed skip report for operator
            if catalist_validators[global_index] == operator.total_exited_validators:
                del catalist_validators[global_index]

        logger.info({'msg': 'Fetch new catalist exited validators by node operator.', 'value': catalist_validators})
        return catalist_validators

    @lru_cache(maxsize=1)
    def get_exited_catalist_validators(self, blockstamp: ReferenceBlockStamp) -> dict[NodeOperatorGlobalIndex, int]:
        catalist_validators = self.w3.catalist_validators.get_catalist_validators_by_node_operators(blockstamp)

        result = {}

        for global_no_index in catalist_validators.keys():
            result[global_no_index] = reduce(
                lambda total, validator: total + int(is_exited_validator(validator, blockstamp.ref_epoch)),
                catalist_validators[global_no_index],
                0,
            )

        return result

    def get_oracle_report_limits(self, blockstamp: BlockStamp) -> OracleReportLimits:
        result = self.w3.catalist_contracts.oracle_report_sanity_checker.functions.getOracleReportLimits().call(
            block_identifier=blockstamp.block_hash,
        )
        orl = named_tuple_to_dataclass(result, OracleReportLimits)
        logger.info({'msg': 'Fetch oracle sanity checks.', 'value': orl})
        return orl

    def _get_last_requested_validator_indices(self, blockstamp: BlockStamp, module: StakingModule, node_operators_ids_in_module: Sequence[int]) -> list[int]:
        return self.w3.catalist_contracts.validators_exit_bus_oracle.functions.getLastRequestedValidatorIndices(
            module.id,
            node_operators_ids_in_module,
        ).call(block_identifier=blockstamp.block_hash)

    def get_recently_requested_but_not_exited_validators(
        self,
        blockstamp: ReferenceBlockStamp,
        chain_config: ChainConfig,
    ) -> list[CatalistValidator]:
        """
        Returns list of validators recently requested to exit (exit deadline slot in future).

        The deadline slot after which validators are delayed:
        validator_delayed_deadline_slot = max(
            (activation_epoch + SHARD_COMMITTEE_PERIOD),  # For validators that were not able to exit cause of restrictions of the chain
            epoch_when_validator_was_requested_to_exit,
        ) * slots_per_epoch + VALIDATOR_DELAYED_TIMEOUT_IN_SLOTS
        """
        catalist_validators_by_operator = self.w3.catalist_validators.get_catalist_validators_by_node_operators(blockstamp)
        ejected_indexes = self.get_operators_with_last_exited_validator_indexes(blockstamp)
        recent_indexes = self.get_recently_requests_to_exit_indexes_by_operators(
            blockstamp, chain_config, catalist_validators_by_operator.keys()
        )

        validators_recently_requested_to_exit = []

        for global_index, validators in catalist_validators_by_operator.items():

            def validator_requested_to_exit(validator: CatalistValidator) -> bool:
                return int(validator.index) <= ejected_indexes[global_index]

            def validator_recently_requested_to_exit(validator: CatalistValidator) -> bool:
                return int(validator.index) in recent_indexes[global_index]

            def validator_eligible_to_exit(validator: CatalistValidator) -> bool:
                delayed_timeout_in_epoch = self.get_validator_delayed_timeout_in_slot(blockstamp) // chain_config.slots_per_epoch
                return is_validator_eligible_to_exit(validator, EpochNumber(blockstamp.ref_epoch - delayed_timeout_in_epoch))

            def is_validator_recently_requested_but_not_exited(validator: CatalistValidator) -> bool:
                if not validator_requested_to_exit(validator):
                    return False

                if is_on_exit(validator):
                    return False

                if validator_recently_requested_to_exit(validator):
                    return True

                if not validator_eligible_to_exit(validator):
                    return True

                return False

            def is_validator_delayed(validator: CatalistValidator) -> bool:
                return (
                    validator_requested_to_exit(validator) and
                    not is_on_exit(validator) and
                    not validator_recently_requested_to_exit(validator)
                )

            validators_recently_requested_to_exit.extend(
                list(filter(is_validator_recently_requested_but_not_exited, validators))
            )
            delayed_validators_count = len(list(filter(is_validator_delayed, validators)))

            ACCOUNTING_DELAYED_VALIDATORS.labels(*global_index).set(delayed_validators_count)

        return validators_recently_requested_to_exit

    def get_recently_requests_to_exit_indexes_by_operators(
        self,
        blockstamp: ReferenceBlockStamp,
        chain_config: ChainConfig,
        operator_global_indexes: Iterable[NodeOperatorGlobalIndex],
    ) -> dict[NodeOperatorGlobalIndex, set[int]]:
        exiting_keys_delayed_border_in_slots = self.get_validator_delayed_timeout_in_slot(blockstamp)

        events = get_events_in_past(
            self.w3.catalist_contracts.validators_exit_bus_oracle.events.ValidatorExitRequest,  # type: ignore[arg-type]
            to_blockstamp=blockstamp,
            for_slots=exiting_keys_delayed_border_in_slots,
            seconds_per_slot=chain_config.seconds_per_slot,
        )

        logger.info({'msg': f'Fetch exit events. Got {len(events)} events.'})

        # Initialize dict with empty sets for operators which validators were not contained in any event
        global_indexes: dict[NodeOperatorGlobalIndex, set[int]] = {
            operator: set() for operator in operator_global_indexes
        }

        for event in events:
            operator_global_index = (event['args']['stakingModuleId'], event['args']['nodeOperatorId'])
            global_indexes[operator_global_index].add(event['args']['validatorIndex'])

        return global_indexes

    @lru_cache(maxsize=1)
    def get_validator_delayed_timeout_in_slot(self, blockstamp: ReferenceBlockStamp) -> int:
        exiting_keys_delayed_border_in_slots_bytes = self.w3.catalist_contracts.oracle_daemon_config.functions.get(
            'VALIDATOR_DELAYED_TIMEOUT_IN_SLOTS'
        ).call(block_identifier=blockstamp.block_hash)

        return self.w3.to_int(exiting_keys_delayed_border_in_slots_bytes)
