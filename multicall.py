from typing import List
from web3.auto import w3
from eth_utils import to_checksum_address
from eth_abi import encode_single, decode_single
from eth_utils import function_signature_to_4byte_selector


def parse_signature(signature):
    """
    Breaks 'func(address)(uint256)' into ['func', '(address)', '(uint256)']
    """
    parts = []
    stack = []
    start = 0
    for end, letter in enumerate(signature):
        if letter == '(':
            stack.append(letter)
            if not parts:
                parts.append(signature[start:end])
                start = end
        if letter == ')':
            stack.pop()
            if not stack:  # we are only interested in outermost groups
                parts.append(signature[start:end + 1])
                start = end + 1
    return parts


class Signature:
    def __init__(self, signature):
        self.signature = signature
        self.parts = parse_signature(signature)
        self.input_types = self.parts[1]
        self.output_types = self.parts[2]
        self.function = ''.join(self.parts[:2])
        self.fourbyte = function_signature_to_4byte_selector(self.function)

    def encode_data(self, args=None):
        return self.fourbyte + encode_single(self.input_types, args) if args else self.fourbyte

    def decode_data(self, output):
        return decode_single(self.output_types, output)


class Call:
    def __init__(self, target, function, returns=None, _w3=None):
        self.target = to_checksum_address(target)

        if isinstance(function, list):
            self.function, *self.args = function
        else:
            self.function = function
            self.args = None

        if _w3 is None:
            self.w3 = w3
        else:
            self.w3 = _w3

        self.signature = Signature(self.function)
        self.returns = returns

    @property
    def data(self):
        return self.signature.encode_data(self.args)

    def decode_output(self, output):
        decoded = self.signature.decode_data(output)
        if self.returns:
            return {
                name: handler(value) if handler else value
                for (name, handler), value
                in zip(self.returns, decoded)
            }
        else:
            return decoded if len(decoded) > 1 else decoded[0]

    def __call__(self, args=None):
        args = args or self.args
        calldata = self.signature.encode_data(args)
        output = self.w3.eth.call({'to': self.target, 'data': calldata})
        return self.decode_output(output)


class Multicall:
    def __init__(self, calls: List[Call], _w3=None, _agg=None):
        self.calls = calls

        if _w3 is None:
            self.w3 = w3
        else:
            self.w3 = _w3

        self.agg = _agg

    def __call__(self):
        aggregate = Call(
            self.agg,
            'aggregate((address,bytes)[])(uint256,bytes[])',
            None,
            self.w3
        )
        args = [[[call.target, call.data] for call in self.calls]]
        block, outputs = aggregate(args)
        result = {}
        for call, output in zip(self.calls, outputs):
            result.update(call.decode_output(output))
        return result

