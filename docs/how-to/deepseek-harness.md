# DeepSeek Harness route removed

The bundled DSH route, profile and runtime have been removed. Minimal is the only
supported study workflow. To use a DeepSeek-compatible API, configure the built-in
API agent through workspace **Connections**, or use `study --backend builtin`
with a private provider configuration.

See [migration to minimal](minimal-migration.md) for command replacements and
handling older records. The historical DSH guide remains in the
[0.6.2 source](https://github.com/tomzhu0225/simjecture/blob/v0.6.2/docs/how-to/deepseek-harness.md).
