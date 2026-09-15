// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

const getModelById = vi.fn(async () => ({
	success: true,
	data: { model: { id: '01MODEL', filename: 'krea2.safetensors', custom_name: 'Krea 2' } }
}));
const getModels = vi.fn(async () => ({ success: true, data: { models: [] } }));

vi.mock('$lib/services/api/index', () => ({
	api: {
		listMemory: vi.fn(async () => ({ success: true, data: { notes: [] } })),
		getModelById,
		getModels,
		createMemory: vi.fn(),
		updateMemory: vi.fn(),
		deleteMemory: vi.fn()
	}
}));
vi.mock('$lib/stores/presetsCatalog', () => ({
	loadPresets: vi.fn(async () => ({ success: true, data: [] }))
}));

const { default: ChatMemoryPanel } = await import('$lib/components/chat/ChatMemoryPanel.svelte');
const { createClassComponent } = await import('svelte/legacy');

const mounted: Array<{ $destroy: () => void }> = [];

function mount(formData: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: ChatMemoryPanel,
		target,
		props: { presetId: null, formData, modeId: 'generation', onClose: () => {} }
	});
	mounted.push(component);
	return target;
}

async function settle() {
	for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0));
}

afterEach(() => {
	while (mounted.length) mounted.pop()!.$destroy();
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('ChatMemoryPanel active model', () => {
	it('resolves a model: ref by id and titles the group with the model name', async () => {
		const target = mount({ checkpoint: { modelPath: 'model:01MODEL' } });
		await settle();
		expect(getModelById).toHaveBeenCalledWith('01MODEL', true);
		expect(getModels).not.toHaveBeenCalled();
		expect(target.textContent).toContain('Model · Krea 2');
		expect(target.textContent).not.toContain('No active model');
	});

	it('still searches by filename for a legacy path value', async () => {
		mount({ checkpoint: { modelPath: 'checkpoints/old.safetensors' } });
		await settle();
		expect(getModels).toHaveBeenCalledWith({ search: 'old.safetensors', limit: 10 });
		expect(getModelById).not.toHaveBeenCalled();
	});
});
