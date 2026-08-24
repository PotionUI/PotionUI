<script lang="ts">
	import GenerationCard from '$lib/components/GenerationCard.svelte';
	import ModelCard from '$lib/components/ModelCard.svelte';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import ComponentExample from './ComponentExample.svelte';

	const now = new Date().toISOString();
	const demoMedia = {
		potion: '/frontend-kit/potion-lab.png',
		portrait: '/frontend-kit/portrait-study.png',
		product: '/frontend-kit/product-study.png'
	};

	function imageFile(id: number, path: string, width: number, height: number) {
		return {
			id,
			file_path: path.split('/').pop() || path,
			file_type: 'IMAGE',
			file_size: 2_480_000,
			is_final: true,
			created_at: now,
			width,
			height,
			thumbnail_small: path,
			thumbnail_medium: path,
			thumbnail_large: path
		};
	}

	const generations: GenerationHistoryItem[] = [
		{
			id: 'kit-complete',
			preset_id: 'z-image/turbo',
			preset_name: 'Z-Image Turbo',
			mode: 'txt2img',
			form_data: {},
			status: 'completed',
			progress: 1,
			created_at: now,
			updated_at: now,
			completed_at: now,
			files: [imageFile(1, demoMedia.potion, 1024, 1536)],
			rating: 5,
			is_favorite: true
		},
		{
			id: 'kit-multiple',
			preset_id: 'sdxl/product',
			preset_name: 'SDXL Product',
			mode: 'txt2img',
			form_data: {},
			status: 'completed',
			progress: 1,
			created_at: now,
			updated_at: now,
			files: [
				imageFile(2, demoMedia.product, 1024, 1024),
				imageFile(3, demoMedia.portrait, 1024, 1024)
			],
			rating: 3,
			is_favorite: false
		},
		{
			id: 'kit-running',
			preset_id: 'flux/standard',
			preset_name: 'Flux Standard',
			mode: 'txt2img',
			form_data: {},
			status: 'running',
			progress: 0.64,
			created_at: now,
			updated_at: now,
			files: [],
			rating: 0,
			is_favorite: false
		},
		{
			id: 'kit-failed',
			preset_id: 'sdxl/realistic',
			preset_name: 'SDXL Realistic',
			mode: 'img2img',
			form_data: {},
			status: 'failed',
			progress: 0.18,
			created_at: now,
			updated_at: now,
			error_message: 'The backend ran out of available memory.',
			files: [],
			rating: 0,
			is_favorite: false
		}
	];

	const models = [
		{
			id: 'kit-model-potion',
			name: 'Potion Lab XL',
			filename: 'potion-lab-xl-v3.safetensors',
			model_type: 'checkpoint',
			tags: [{ name: 'Photorealistic' }],
			providers: [{ provider: 'civitai-provider', tags: ['Portrait'] }],
			file_size: 6_940_000_000,
			files: [{ file_type: 'image', url: demoMedia.potion }],
			backend_ids: ['native-0', 'comfy-main'],
			is_favorite: true
		},
		{
			id: 'kit-model-product',
			name: 'Product Studio LoRA',
			filename: 'product-studio-detail-v2.safetensors',
			model_type: 'lora',
			tags: [{ name: 'Product' }],
			providers: [{ provider: 'huggingface-provider', tags: ['Studio'] }],
			file_size: 248_000_000,
			files: [
				{ file_type: 'image', url: demoMedia.product },
				{ file_type: 'image', url: demoMedia.portrait }
			],
			backend_ids: ['comfy-main'],
			is_favorite: false
		},
		{
			id: 'kit-model-vae',
			name: 'Clear Detail VAE',
			filename: 'clear-detail-vae.safetensors',
			model_type: 'vae',
			files: [],
			backend_ids: [],
			is_favorite: false
		},
		{
			id: 'kit-model-unknown',
			name: 'Unindexed Model',
			filename: 'unindexed-model-v1.ckpt',
			model_type: 'checkpoint',
			files: [{ file_type: 'image', url: demoMedia.portrait }],
			is_favorite: false
		}
	];

	let selectedGenerationIds = new Set<string>(['kit-multiple']);
	let selectedModelIds = new Set<string>(['kit-model-product']);

	function toggleGeneration(generation: GenerationHistoryItem) {
		const next = new Set(selectedGenerationIds);
		next.has(generation.id) ? next.delete(generation.id) : next.add(generation.id);
		selectedGenerationIds = next;
	}

	function toggleModel(model: { id: string }) {
		const next = new Set(selectedModelIds);
		next.has(model.id) ? next.delete(model.id) : next.add(model.id);
		selectedModelIds = next;
	}
</script>

<div class="space-y-8">
	<ComponentExample
		title="GenerationCard"
		description="Completed media, multiple outputs, running progress, failure, and local selection behavior. Resize the window to verify the responsive grid."
		code={`<GenerationCard\n  {generation}\n  selectable\n  showCheckbox\n  selected={selectedIds.has(generation.id)}\n  onSelect={toggleGeneration}\n/>`}
	>
		<div class="grid w-full grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
			{#each generations as generation (generation.id)}
				<GenerationCard
					{generation}
					selectable
					showCheckbox
					selected={selectedGenerationIds.has(generation.id)}
					onSelect={toggleGeneration}
					showActions={false}
				/>
			{/each}
		</div>
	</ComponentExample>

	<ComponentExample
		title="ModelCard"
		description="Checkpoint, LoRA, missing-media, indexed availability, unknown availability, multiple media, and selection states."
		code={`<ModelCard\n  {model}\n  showTechnical\n  availabilityIndexed\n  selectable\n  showCheckbox\n  selected={selectedIds.has(model.id)}\n  onSelect={toggleModel}\n/>`}
	>
		<div class="grid w-full grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
			{#each models as model (model.id)}
				<ModelCard
					{model}
					showTechnical
					availabilityIndexed={model.id !== 'kit-model-unknown'}
					backendNames={{ 'native-0': 'Native GPU', 'comfy-main': 'ComfyUI Main' }}
					selectable
					showCheckbox
					selected={selectedModelIds.has(model.id)}
					onSelect={toggleModel}
				/>
			{/each}
		</div>
	</ComponentExample>
</div>
