<script lang="ts">
	import { propReadProbe } from './propReadProbe';

	let props: Record<string, unknown> = $props();

	for (const key of propReadProbe.watched) {
		$effect(() => {
			void props[key];
			propReadProbe.record(key);
		});
	}
</script>
