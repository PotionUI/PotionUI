<script lang="ts">
	import { run } from './modelCardActionControl';

	export let context: { track?: (fn: () => Promise<void>) => Promise<void> } = {};

	function handleClick() {
		context.track ? context.track(run) : run();
	}
</script>

<button data-testid="stub-model-card-action" on:click={handleClick}>run</button>
