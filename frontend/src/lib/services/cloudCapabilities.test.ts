import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createCapabilityTracker, CapabilityCache } from '$lib/form/capabilityTracker';

const get = vi.fn();
const getModelById = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: { getClient: () => ({ get }), getModelById }
}));

const { fetchCloudCapabilities, noteModelKind, forgetModelKinds, NotCloudModelError } = await import('./cloudCapabilities');

const CAPS = { params: [], inputs: [] };

beforeEach(() => {
	get.mockReset().mockResolvedValue({ data: { success: true, data: CAPS } });
	getModelById.mockReset();
	forgetModelKinds();
});

describe('fetchCloudCapabilities', () => {
	it('never requests capabilities for a model already known to be local', async () => {
		noteModelKind({ id: 'local1', model_type: 'checkpoint' });
		await expect(fetchCloudCapabilities('local1')).rejects.toBeInstanceOf(NotCloudModelError);
		expect(get).not.toHaveBeenCalled();
		expect(getModelById).not.toHaveBeenCalled();
	});

	it('requests capabilities for a model known to be cloud', async () => {
		noteModelKind({ id: 'cloud1', model_type: 'cloud' });
		await expect(fetchCloudCapabilities('cloud1')).resolves.toMatchObject({ params: [] });
		expect(get).toHaveBeenCalledWith('/api/cloud/models/cloud1/capabilities');
		expect(getModelById).not.toHaveBeenCalled();
	});

	it('resolves an unseen model once and skips capabilities when it is local', async () => {
		getModelById.mockResolvedValue({ success: true, data: { model: { id: 'x', model_type: 'lora' } } });
		await expect(fetchCloudCapabilities('x')).rejects.toBeInstanceOf(NotCloudModelError);
		await expect(fetchCloudCapabilities('x')).rejects.toBeInstanceOf(NotCloudModelError);
		expect(getModelById).toHaveBeenCalledTimes(1);
		expect(get).not.toHaveBeenCalled();
	});

	it('resolves an unseen cloud model and then fetches its capabilities', async () => {
		getModelById.mockResolvedValue({ success: true, data: { model: { model_type: 'cloud' } } });
		await fetchCloudCapabilities('y');
		expect(get).toHaveBeenCalledTimes(1);
	});

	it('does not cache a failed resolution as local', async () => {
		getModelById.mockResolvedValueOnce({ success: false }).mockResolvedValueOnce({ success: true, data: { model: { model_type: 'cloud' } } });
		await expect(fetchCloudCapabilities('z')).rejects.toThrow();
		await expect(fetchCloudCapabilities('z')).resolves.toBeDefined();
	});

	it('keeps a tracker from hitting the capabilities endpoint for a local selection', async () => {
		noteModelKind({ id: 'local2', model_type: 'checkpoint' });
		const onLoaded = vi.fn();
		const tracker = createCapabilityTracker({ fetch: fetchCloudCapabilities, onLoaded, cache: new CapabilityCache() });
		tracker.select('model', 'local2');
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(get).not.toHaveBeenCalled();
		expect(onLoaded).not.toHaveBeenCalled();
	});
});
