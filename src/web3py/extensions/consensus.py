from web3 import Web3
from web3.module import Module

from src.providers.consensus.client import ConsensusClient
from src.variables import HTTP_REQUEST_TIMEOUT_CONSENSUS


class ConsensusClientModule(ConsensusClient, Module):
    def __init__(self, hosts: list[str], w3: Web3):
        self.w3 = w3

        super(ConsensusClient, self).__init__(hosts, HTTP_REQUEST_TIMEOUT_CONSENSUS)
        super(Module, self).__init__()
