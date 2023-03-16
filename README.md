# Ethereum Event Listener

A generic and reusable Ethereum event listener framework that can be easily configured to handle any contract and event.

## Requirements

- Python 3.7+
- `web3` Python library

## Installation

1. Clone the repository or download the source files.
2. Install the required Python libraries by running the following command in the directory containing the `requirements.txt` file:

```bash
pip install -r requirements.txt
```

## Usage

1. Update the `config.json` file with the appropriate values for your Ethereum node provider, contract address, contract ABI, and event name.
2. Implement your custom event handling function in the `event_handlers.py` file. The function should be named `handle_EVENTNAME`, where `EVENTNAME` is the name of the event you want to listen for.
3. Run the main program:

```bash
python main.py
```

This will start the Ethereum event listener, which will fetch and process events according to the specified configuration.

## Customization

To use this framework with other contracts and events, follow these steps:

1. Update the `config.json` file with the new contract address, ABI, and event name.
2. Add a new event handling function in the `event_handlers.py` file, following the naming convention `handle_EVENTNAME`.
3. Run the main program as described in the Usage section.

## License

This project is released under the MIT License.
