# vim:ft=make:noexpandtab:
.PHONY: format
.DEFAULT_GOAL := help

format: ## Run python formatter
	find . -type f -name '*.py' | grep -v .eggs | xargs yapf -i

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
