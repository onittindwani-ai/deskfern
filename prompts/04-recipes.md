# Recipes for tools without a built in adapter

For a help desk or knowledge source that is not in the Integrations page, ask:

Read apps/api/src/deskfern/adapters/base.py and one existing adapter in helpdesks.py. Write a new adapter for <tool> that follows the same shape, reads the vendor's current API docs and links the page you used, never sends anything in draft mode, and adds tests against a local mock of the vendor in mock/. Do not use a real key.
