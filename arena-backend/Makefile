.PHONY: setup migrate serve test integration sandbox-test sandbox-calibrate experimental-test schema time-bank-test
PYTHON := .venv/bin/python
# Re-enter by absolute path before every process. On the WSL /mnt/c workspace,
# Python's getpath has failed with an inherited cwd during integration.
# A fresh cd resolves the directory again before Python initializes.

setup:
	cd "$(CURDIR)" && python3 -m venv .venv
	cd "$(CURDIR)" && $(PYTHON) -m pip install -r requirements-dev.txt

migrate:
	cd "$(CURDIR)" && $(PYTHON) -m arena_api migrate

serve:
	cd "$(CURDIR)" && $(PYTHON) -m arena_api serve

test:
	# Hermetic: never read the operator's ~/.uet-arena config or secrets during unit tests.
	cd "$(CURDIR)" && ARENA_CONFIG=none ARENA_SECRETS=none $(PYTHON) -m unittest discover -s tests -v

integration:
	cd "$(CURDIR)" && ARENA_CONFIG=none ARENA_SECRETS=none $(PYTHON) tests/web_integration.py
	cd "$(CURDIR)" && ARENA_CONFIG=none ARENA_SECRETS=none $(PYTHON) tests/competition_integration.py

experimental-test:
	cd "$(CURDIR)" && $(PYTHON) -m unittest discover -s tests -p test_experimental.py -v
	cd "$(CURDIR)" && $(PYTHON) -m unittest discover -s tests -p test_addons.py -v
	cd "$(CURDIR)" && $(PYTHON) tests/experimental_parity.py
	cd "$(CURDIR)" && $(PYTHON) tests/experimental_integration.py

schema:
	cd "$(CURDIR)" && ARENA_CONFIG=none ARENA_SECRETS=none $(PYTHON) -c 'import json; from arena_api.app import create_app; print(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2))' > docs/openapi.json

time-bank-test:
	cd "$(CURDIR)" && $(PYTHON) tests/time_bank_integration.py

# Shared destructive-bot suite (TAI-LIEU.docx). BACKEND=bwrap|windows; on Windows run
# "python tests/sandbox_suite/suite.py --backend windows" from PowerShell.
BACKEND ?= bwrap
sandbox-test:
	cd "$(CURDIR)" && $(PYTHON) tests/sandbox_suite/suite.py --backend $(BACKEND)

sandbox-calibrate:
	cd "$(CURDIR)" && $(PYTHON) tests/sandbox_suite/suite.py --backend $(BACKEND) --calibrate
