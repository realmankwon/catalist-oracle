from functools import lru_cache
from typing import List, Dict, Tuple, TypedDict

from eth_typing import Address
from web3.module import Module

from src.providers.consensus.typings import Validator
from src.providers.keys.typings import LidoKey, OperatorResponse, OperatorExpanded
from src.typings import BlockStamp
from src.utils.freeze_decorator import freezeargs


class LidoValidator(TypedDict):
    key: LidoKey
    validator: Validator


NodeOperatorIndex = Tuple[Address, int]


class LidoValidatorsProvider(Module):
    @freezeargs
    @lru_cache(maxsize=1)
    def get_lido_validators(self, blockstamp: BlockStamp) -> List[LidoValidator]:
        lido_keys = self.w3.kac.get_all_lido_keys(blockstamp)
        validators = self.w3.cc.get_validators(blockstamp['state_root'])

        return self._merge_validators(lido_keys, validators)

    @staticmethod
    def _merge_validators(keys: List[LidoKey], validators: List[Validator]) -> List[LidoValidator]:
        """Merging and filter non-lido validators."""
        validators_keys_dict = {validator['validator']['pubkey']: validator for validator in validators}

        lido_validators = []

        for key in keys:
            if key['key'] in validators_keys_dict:
                lido_validators.append(LidoValidator(
                    key=key,
                    validator=validators_keys_dict[key['key']],
                ))

        return lido_validators

    @freezeargs
    @lru_cache(maxsize=1)
    def get_lido_validators_by_node_operators(self, blockstamp: BlockStamp) -> Dict[NodeOperatorIndex, LidoValidator]:
        merged_validators = self.get_lido_validators(blockstamp)
        no_operators = self.get_lido_node_operators(blockstamp)

        # Make sure even empty NO will be presented in dict
        no_validators = {(operator['stakingModuleAddress'], operator['index']): [] for operator in no_operators}

        for validator in merged_validators:
            no_validators[(validator['key']['moduleAddress'], validator['key']['operatorIndex'])].append(validator)

        return dict(no_validators)

    @freezeargs
    @lru_cache(maxsize=1)
    def get_lido_node_operators(self, blockstamp: BlockStamp) -> List[OperatorExpanded]:
        operators_by_modules: list[OperatorResponse] = self.w3.kac.get_operators(blockstamp)

        operators = []

        for module in operators_by_modules:
            operators.extend([
                OperatorExpanded(
                    stakingModuleAddress=module['module']['stakingModuleAddress'],
                    **operator,
                )
                for operator in module['operators']
            ])

        return operators
