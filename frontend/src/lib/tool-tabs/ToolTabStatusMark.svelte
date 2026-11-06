<script lang="ts">
	import type { ToolTabStatus } from '$lib/types/tabs';

	let { status }: { status: ToolTabStatus } = $props();
</script>

<span class="mark font-mono text-xs font-normal tabular-nums" data-testid="tab-status" data-tone={status.tone} aria-label={status.label} role="img">
	<span class="dot" class:ring={status.ring} class:pulse={status.pulse} class:danger={status.tone === 'danger'} class:success={status.tone === 'success'}></span>
	{#if status.count}<span aria-hidden="true">{status.count[0]}/{status.count[1]}</span>{/if}
</span>

<style>
	.mark {
		display: inline-flex;
		flex-shrink: 0;
		align-items: center;
		gap: 5px;
		color: rgb(var(--signal));
	}

	.dot {
		width: 7px;
		height: 7px;
		flex-shrink: 0;
		border-radius: 50%;
		background: rgb(var(--signal));
	}

	.ring {
		box-shadow: 0 0 0 3px rgb(var(--signal) / 0.25);
	}

	.pulse {
		animation: mark-pulse 1.6s ease-in-out infinite;
	}

	.danger {
		background: rgb(var(--danger));
	}

	.success {
		background: rgb(var(--success));
	}

	@keyframes mark-pulse {
		50% {
			opacity: 0.35;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.pulse {
			animation: none;
		}
	}
</style>
