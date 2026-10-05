.PHONY: bootstrap demo test naive compare

bootstrap:
	python -m careops.bootstrap

demo: bootstrap
	python -m careops.cli --role analyst "Why did in-network appointment availability in Texas decline last week?"

test: bootstrap
	python -m pytest -q

naive:
	python examples/naive_rag.py "network eligibility credentialing escalation"

compare: bootstrap
	python examples/compare_rag.py
