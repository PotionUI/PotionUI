<script>
	let { generationIds = [], generations = [], onClose } = $props();

	let exporting = $state(false);
	let errorMessage = $state('');

	function isImageFile(file) {
		return (file?.file_type || '').toUpperCase() === 'IMAGE';
	}

	let generationCount = $derived(generationIds.length);
	let imageCount = $derived(
		generations.reduce((total, generation) => total + (generation.files || []).filter(isImageFile).length, 0)
	);

	function authHeaders() {
		const token = localStorage.getItem('auth_token');
		return token ? { Authorization: `Bearer ${token}` } : {};
	}

	function notify(level, message) {
		window.__potionui?.notifications?.toast(level, message);
	}

	async function errorFromResponse(response) {
		try {
			const payload = await response.json();
			const detail = payload?.detail;
			if (typeof detail === 'string') return detail;
			if (detail?.message) return detail.message;
		} catch {
			return `Export failed (${response.status})`;
		}
		return `Export failed (${response.status})`;
	}

	async function download() {
		if (exporting || generationIds.length === 0) return;
		exporting = true;
		errorMessage = '';
		try {
			const response = await fetch('/api/plugins/civitai-provider/export-zip', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json', ...authHeaders() },
				body: JSON.stringify({ generation_ids: generationIds })
			});
			if (!response.ok) throw new Error(await errorFromResponse(response));

			const blob = await response.blob();
			const url = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = `civitai-export-${generationIds.length}.zip`;
			document.body.appendChild(anchor);
			anchor.click();
			anchor.remove();
			URL.revokeObjectURL(url);

			notify('success', 'CivitAI export downloaded');
			onClose?.();
		} catch (e) {
			errorMessage = e instanceof Error ? e.message : 'Export failed.';
			notify('error', 'CivitAI export failed');
		} finally {
			exporting = false;
		}
	}
</script>

<div class="body">
	<p class="summary">
		{generationCount} generation{generationCount === 1 ? '' : 's'}, {imageCount} image{imageCount === 1 ? '' : 's'} to
		export.
	</p>
	<p class="hint">CivitAI reads the embedded parameters chunk on upload and auto-fills the prompt, sampler, model, and LoRAs.</p>

	{#if errorMessage}
		<p class="message message-error">{errorMessage}</p>
	{/if}

	<div class="actions">
		<button class="btn btn-secondary" onclick={onClose} disabled={exporting}>Cancel</button>
		<button class="btn btn-primary" onclick={download} disabled={exporting || imageCount === 0}>
			{#if exporting}<span class="spinner"></span>{/if}
			Download ZIP
		</button>
	</div>
</div>

<style>
	.body {
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.summary {
		margin: 0;
		font-size: 13px;
		font-variant-numeric: tabular-nums;
		color: rgb(var(--fg, 232 234 237));
	}
	.hint {
		margin: 0;
		font-size: 12px;
		color: rgb(var(--fg-muted, 169 174 184));
	}
	.message {
		padding: 10px 14px;
		border-radius: 6px;
		font-size: 12px;
	}
	.message-error {
		color: rgb(var(--danger, 255 138 138));
		background: rgb(var(--danger, 255 138 138) / 0.1);
		border: 1px solid rgb(var(--danger, 255 138 138) / 0.25);
	}
	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 4px;
	}
	.btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		padding: 8px 16px;
		font-size: 13px;
		font-weight: 500;
		border-radius: 4px;
		border: none;
		cursor: pointer;
		transition: background-color 0.1s;
	}
	.btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.btn-secondary {
		background: rgb(var(--surface-3, 39 42 49));
		color: rgb(var(--fg, 232 234 237));
	}
	.btn-secondary:not(:disabled):hover {
		background: rgb(var(--line-hover, 58 62 71));
	}
	.btn-primary {
		background: rgb(var(--accent, 255 255 255));
		color: rgb(var(--accent-contrast, 22 22 22));
	}
	.btn-primary:not(:disabled):hover {
		background: rgb(var(--accent-hover, 230 230 230));
	}
	.spinner {
		width: 12px;
		height: 12px;
		border-radius: 50%;
		border: 2px solid rgb(var(--accent-contrast, 22 22 22) / 0.35);
		border-top-color: rgb(var(--accent-contrast, 22 22 22));
		animation: spin 0.7s linear infinite;
		flex-shrink: 0;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
