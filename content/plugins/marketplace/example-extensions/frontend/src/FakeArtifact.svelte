<script>
	/**
	 * Reference `history.artifact` renderer (A5), registered for
	 * `artifact_type: "example_fake_artifact"`. Receives the same
	 * `{ artifact, onSeedClick }` props as every core artifact component
	 * (see `frontend/src/lib/registries/artifactRendererRegistry.ts`).
	 */
	export let artifact;
	export let onSeedClick = undefined;
</script>

<div class="example-fake-artifact">
	<strong>Example plugin artifact</strong>
	<pre>{JSON.stringify(artifact?.data ?? artifact, null, 2)}</pre>
</div>

<style>
	.example-fake-artifact {
		padding: 0.5rem;
		border: 1px dashed currentColor;
		border-radius: 0.375rem;
		font-size: 0.75rem;
	}
	pre {
		white-space: pre-wrap;
		word-break: break-word;
		margin: 0.25rem 0 0;
	}
</style>
