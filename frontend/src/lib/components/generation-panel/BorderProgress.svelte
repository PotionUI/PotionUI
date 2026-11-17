<script lang="ts">
	import type { BorderProgress } from './borderProgress';

	let { progress, label = 'Generation progress' }: { progress: BorderProgress; label?: string } = $props();
</script>

<div
	class="border-progress"
	role="progressbar"
	aria-label={label}
	aria-valuemin="0"
	aria-valuemax="100"
	aria-valuenow={progress.state === 'running' ? (progress.percent ?? undefined) : undefined}
	hidden={progress.state === 'idle'}
	data-border-progress={progress.state}
>
	<div class="border-progress-fill" style={progress.state === 'running' ? `width: ${progress.percent}%` : ''}></div>
</div>

<style>
	.border-progress {
		position: absolute;
		z-index: 2;
		top: -1px;
		left: 0;
		right: 0;
		height: 3px;
		overflow: hidden;
		background: rgb(var(--signal) / 0.14);
		pointer-events: none;
	}

	.border-progress[hidden] {
		display: none;
	}

	.border-progress-fill {
		width: 0;
		height: 100%;
		background: rgb(var(--signal));
		transition: width 320ms ease;
	}

	.border-progress[data-border-progress='loading'] .border-progress-fill {
		width: 32%;
		animation: indeterminate 1.25s cubic-bezier(0.4, 0, 0.2, 1) infinite;
	}

	@media (prefers-reduced-motion: reduce) {
		.border-progress-fill {
			animation: none !important;
			transition: none !important;
		}
	}
</style>
