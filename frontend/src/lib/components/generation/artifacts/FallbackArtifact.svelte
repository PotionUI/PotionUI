<script lang="ts">
	export let artifact: { artifact_data: any };
</script>

<pre class="text-xs overflow-auto bg-surface-2 p-2">{JSON.stringify(artifact.artifact_data, null, 2)}</pre>
