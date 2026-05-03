.PHONY: test test-unit test-property test-api install

install:
	pip install -r requirements.txt

test: test-unit test-property test-api

test-unit:
	python -m pytest tests/test_impact_model.py api/tests/test_parameters.py -v

test-property:
	python -m pytest tests/test_market_impact_properties.py -v

test-api:
	python -m pytest api/tests/test_routes.py -v
