<script lang="ts">
	let { tone = 'signal', pulse = false, ring = false }: { tone?: string; pulse?: boolean; ring?: boolean } = $props();
</script>

<span class="dot tone-{tone}" class:pulse class:ring aria-hidden="true"></span>

<style>
	.dot {
		display: inline-block;
		width: 7px;
		height: 7px;
		flex-shrink: 0;
		border-radius: 50%;
		background: rgb(var(--fg-subtle));
	}

	.tone-signal {
		background: rgb(var(--signal));
	}

	.tone-danger {
		background: rgb(var(--danger));
	}

	.tone-success {
		background: rgb(var(--success));
	}

	.tone-warning {
		background: rgb(var(--warning));
	}

	.ring {
		box-shadow: 0 0 0 3px rgb(var(--signal) / 0.25);
	}

	.pulse {
		animation: dot-pulse 1.6s ease-in-out infinite;
	}

	@keyframes dot-pulse {
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
