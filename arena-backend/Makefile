.PHONY: setup migrate serve test integration experimental-test experimental-load-test load-test guest-load-test schema time-bank-test
PYTHON := .venv/bin/python
EXPERIMENTAL_PROOF := experimental-proof-$(shell date +%s)
# Re-enter by absolute path before every process. On the WSL /mnt/c workspace,
# Python's getpath has failed with an inherited cwd after Docker integration.
# A fresh cd resolves the directory again before Python initializes.

setup:
	cd "$(CURDIR)" && python3 -m venv .venv
	cd "$(CURDIR)" && $(PYTHON) -m pip install -r requirements-dev.txt

migrate:
	cd "$(CURDIR)" && $(PYTHON) -m arena_api migrate

serve:
	cd "$(CURDIR)" && $(PYTHON) -m arena_api serve

test:
	cd "$(CURDIR)" && $(PYTHON) -m unittest discover -s tests -v

integration:
	cd "$(CURDIR)" && docker build -t uet-arena-worker:v1 -t uet-arena-worker:clock-v2 -t uet-arena-worker:experimental-clock-v2 .
	cd "$(CURDIR)" && $(PYTHON) tests/integration.py
	cd "$(CURDIR)" && $(PYTHON) tests/web_integration.py
	cd "$(CURDIR)" && $(PYTHON) tests/competition_integration.py

load-test:
	cd "$(CURDIR)" && $(PYTHON) tests/load_resilience.py

experimental-test:
	cd "$(CURDIR)" && docker build -t uet-arena-worker:experimental-v1 -t uet-arena-worker:clock-v2 -t uet-arena-worker:experimental-clock-v2 .
	cd "$(CURDIR)" && $(PYTHON) -m unittest discover -s tests -p test_experimental.py -v
	cd "$(CURDIR)" && $(PYTHON) -m unittest discover -s tests -p test_addons.py -v
	cd "$(CURDIR)" && $(PYTHON) tests/experimental_parity.py
	cd "$(CURDIR)" && $(PYTHON) tests/experimental_integration.py

experimental-load-test:
	cd "$(CURDIR)" && docker build -t uet-arena-worker:experimental-v1 -t uet-arena-worker:clock-v2 -t uet-arena-worker:experimental-clock-v2 .
	cd "$(CURDIR)" && ARENA_EXPERIMENTAL_PROOF_DATA="$(EXPERIMENTAL_PROOF)" $(PYTHON) tests/experimental_integration.py
	cd "$(CURDIR)" && $(PYTHON) tests/experimental_load.py --proof-dir "$(EXPERIMENTAL_PROOF)"

guest-load-test:
	cd "$(CURDIR)" && $(PYTHON) tests/guest_load.py

schema:
	cd "$(CURDIR)" && $(PYTHON) -c 'import json; from arena_api.app import create_app; print(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2))' > docs/openapi.json

time-bank-test:
	cd "$(CURDIR)" && docker build -t uet-arena-worker:clock-v2 -t uet-arena-worker:experimental-clock-v2 .
	cd "$(CURDIR)" && $(PYTHON) tests/time_bank_integration.py
