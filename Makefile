.PHONY: test test-unit test-property install

install:
	pip install -r requirements.txt

test: test-unit test-property

test-unit:
	python -m pytest tests/test_impact_model.py -v

test-property:
	python -m pytest tests/test_market_impact_properties.py -v
