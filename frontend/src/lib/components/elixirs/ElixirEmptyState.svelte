<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';

	let { onstart }: { onstart: (kind: 'preset' | 'media') => void } = $props();
</script>

<div class="dot-grid flex min-h-0 flex-1 overflow-y-auto" data-testid="elixir-empty">
	<div class="empty">
		<span class="icon-tile"><Icon name="flask" className="h-5 w-5" /></span>
		<h2 class="text-lg font-semibold text-fg">Start your elixir</h2>
		<p class="text-sm text-fg-muted">An elixir chains steps. One step's pictures or clips become the next step's input, and you pick what continues.</p>
		<div class="options">
			<button type="button" class="option" onclick={() => onstart('preset')}>
				<Icon name="flask" className="h-[18px] w-[18px] text-fg-muted" />
				<span class="text-sm font-semibold text-fg">Preset step</span>
				<span class="text-sm text-fg-muted">Make images or video from a prompt</span>
			</button>
			<button type="button" class="option" onclick={() => onstart('media')}>
				<Icon name="image" className="h-[18px] w-[18px] text-fg-muted" />
				<span class="text-sm font-semibold text-fg">Files</span>
				<span class="text-sm text-fg-muted">Begin from images in History or the Library</span>
			</button>
		</div>
	</div>
</div>

<style>
	.empty {
		margin: 56px auto 0;
		display: flex;
		max-width: 640px;
		flex-direction: column;
		align-items: center;
		gap: 8px;
		padding: 0 16px 24px;
		text-align: center;
	}

	.empty p {
		max-width: 46ch;
	}

	.icon-tile {
		display: grid;
		width: 44px;
		height: 44px;
		margin-bottom: 6px;
		place-items: center;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 6px;
		background: rgb(var(--surface-2));
		color: rgb(var(--fg-muted));
	}

	.options {
		display: grid;
		width: 100%;
		margin-top: 18px;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
		text-align: left;
	}

	.option {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 6px;
		padding: 14px;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 6px;
		background: rgb(var(--surface-1));
		text-align: left;
	}

	.option:hover,
	.option:focus-visible {
		border-color: rgb(var(--line-hover));
		background: rgb(var(--surface-2));
		outline: none;
	}

	@media (max-width: 479px) {
		.options {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
