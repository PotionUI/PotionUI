<script lang="ts">
	import { untrack } from "svelte";
	import ModelDetailPage from '../../../src/routes/admin/components/models/ModelDetailPage.svelte';

	let { id }: { id: string } = $props();
	let selected = $state({ id: "" });
	selected = { id: untrack(() => id) };

	export function replaceSelected() {
		selected = { id: untrack(() => id) };
	}
</script>

<ModelDetailPage modelId={selected.id} onBack={() => {}} onDeleted={() => {}} onAssignChanged={() => {}} />
