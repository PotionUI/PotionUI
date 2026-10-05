<script lang="ts">
	import type { LimitState } from '../meApi';
	import { barTone } from '../limitView';

	let {
		percent,
		state,
		label,
		class: className = ''
	}: { percent: number; state: LimitState; label: string; class?: string } = $props();

	const tones = { signal: 'bg-signal', warning: 'bg-warning', danger: 'bg-danger' } as const;
	let tone = $derived(tones[barTone(state)]);
</script>

<div
	class="h-1.5 w-full rounded-sm bg-surface-3 overflow-hidden {className}"
	role="progressbar"
	aria-label={label}
	aria-valuemin={0}
	aria-valuemax={100}
	aria-valuenow={percent}
>
	<div class="h-full rounded-sm {tone}" style="width: {percent}%" data-testid="limit-bar-fill"></div>
</div>
