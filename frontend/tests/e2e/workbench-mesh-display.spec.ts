import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// A generated 3D mesh (TRELLIS.2 emits a .glb) must actually display in the
// workbench. The failure this guards against: `displayImage` in
// Workbench.svelte is just the gallery item's URL and knows nothing about
// file_type, so a mesh item satisfied the image branch and rendered as a
// broken <img> while MeshPreview (<model-viewer>) was never reached — and the
// fallback-renderer resolver was itself gated on `!displayImage`, so the mesh
// renderer never even resolved.
//
// No GPU in e2e, so the mesh enters the system through the real upload API
// (a .glb upload is stored as a MESH file row, same as a generated one) and
// reaches the workbench through the real restore path: the tab's persisted
// `activeGenerationId` makes `restoreActiveGenerations` fetch the completed
// generation and populate `batchMeshes` on reload. That crosses upload →
// history API (UPPERCASE file_type, no url field) → `mapGenerationFiles` →
// tabs store → Workbench display chain → renderer registry → MeshPreview.

const JOURNEY = 'workbench-mesh-display';
const TABS_STORAGE_KEY = 'potionui_tabs_state';

// <model-viewer> needs a WebGL context; these flags request the SwiftShader
// software rasterizer for GPU-less machines. In a container whose GPU process
// can't even software-rasterize, THREE still logs context errors but
// <model-viewer> parses the GLB and fires `load` - the spec then proves the
// entire pipeline short of painted pixels. On hardware with working
// GL/SwiftShader the same spec verifies the actual render.
test.use({
	launchOptions: {
		args: [
			'--disable-gpu',
			'--use-gl=angle',
			'--use-angle=swiftshader',
			'--enable-unsafe-swiftshader'
		]
	}
});

/** Smallest valid glTF-2.0 binary: one triangle, no materials (~600 bytes). */
function buildTinyGlb(): Buffer {
	const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
	const indices = new Uint16Array([0, 1, 2, 0]); // 4th entry pads bin to 4-byte alignment
	const bin = Buffer.concat([Buffer.from(positions.buffer), Buffer.from(indices.buffer)]);

	const json = {
		asset: { version: '2.0' },
		scene: 0,
		scenes: [{ nodes: [0] }],
		nodes: [{ mesh: 0 }],
		meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
		buffers: [{ byteLength: bin.length }],
		bufferViews: [
			{ buffer: 0, byteOffset: 0, byteLength: 36 },
			{ buffer: 0, byteOffset: 36, byteLength: 6 }
		],
		accessors: [
			{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] },
			{ bufferView: 1, componentType: 5123, count: 3, type: 'SCALAR' }
		]
	};
	let jsonStr = JSON.stringify(json);
	while (jsonStr.length % 4 !== 0) jsonStr += ' ';
	const jsonBuf = Buffer.from(jsonStr);

	const header = Buffer.alloc(12);
	header.writeUInt32LE(0x46546c67, 0); // magic 'glTF'
	header.writeUInt32LE(2, 4);
	header.writeUInt32LE(12 + 8 + jsonBuf.length + 8 + bin.length, 8);

	const jsonChunk = Buffer.alloc(8);
	jsonChunk.writeUInt32LE(jsonBuf.length, 0);
	jsonChunk.writeUInt32LE(0x4e4f534a, 4); // 'JSON'

	const binChunk = Buffer.alloc(8);
	binChunk.writeUInt32LE(bin.length, 0);
	binChunk.writeUInt32LE(0x004e4942, 4); // 'BIN\0'

	return Buffer.concat([header, jsonChunk, jsonBuf, binChunk, bin]);
}

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

test('workbench renders a generated mesh through <model-viewer>', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	// --- Seed: upload a .glb as a completed generation (stored as a MESH row).
	const uploadRes = await page.request.post('/api/generations/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: {
			files: {
				name: 'workbench-mesh-display.glb',
				mimeType: 'model/gltf-binary',
				buffer: buildTinyGlb()
			}
		}
	});
	expect(uploadRes.ok(), `glb upload -> ${uploadRes.status()}: ${await uploadRes.text()}`).toBeTruthy();
	const uploadBody = await uploadRes.json();
	const generationId = uploadBody.data.generation_id as string;

	const fileTypes = (uploadBody.data.files as Array<{ file_type: string }>).map((f) => f.file_type);
	expect(fileTypes, 'uploaded .glb must be stored as MESH, not IMAGE').toEqual(['MESH']);

	// --- The workbench pane only mounts when the tab has a preset AND a mode
	// selected (`+page.svelte` renders a placeholder otherwise), so install
	// and assign any preset - nothing is generated with it.
	const me = await apiGet(page, '/api/auth/me', token);
	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const preset = ((list.data || []) as Array<{ id: string; installed?: boolean }>)[0];
	if (!preset) {
		test.skip(true, 'No presets available on this throwaway instance.');
		return;
	}
	if (!preset.installed) {
		await apiPost(page, `/api/presets/${preset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [me.data.id] });
	const modes = await apiGet(page, `/api/presets/${preset.id}/modes`, token);
	const modeName = modes.data?.modes?.[0]?.name as string | undefined;
	if (!modeName) {
		test.skip(true, `Preset ${preset.id} exposes no modes on this throwaway instance.`);
		return;
	}

	// --- Point the active tab at the seeded generation (and at the preset, so
	// the workbench pane mounts), then reload so the restore path loads it.
	// This must run as an init script: it executes on the reloaded document
	// BEFORE the app boots, after which no debounced store save can clobber
	// it - a plain evaluate+reload races against the tabs store's
	// 500ms-debounced persistence.
	await page.addInitScript(
		({ key, genId, presetId, mode }) => {
			const raw = localStorage.getItem(key);
			if (!raw) return;
			const state = JSON.parse(raw);
			if (!state?.tabs?.length) return;
			state.tabs[0].activeGenerationId = genId;
			state.tabs[0].selectedPreset = presetId;
			state.tabs[0].selectedMode = mode;
			localStorage.setItem(key, JSON.stringify(state));
		},
		{ key: TABS_STORAGE_KEY, genId: generationId, presetId: preset.id, mode: modeName }
	);

	// Surface the restore path's two API calls in the test output - when the
	// viewer never appears, which half failed is the first question.
	page.on('response', (res) => {
		if (res.url().includes(`/api/generations/`) && res.url().includes(generationId)) {
			console.log(`[${JOURNEY}] restore call: ${res.status()} ${res.url()}`);
		}
	});
	page.on('console', (msg) => {
		if (msg.type() === 'error' || msg.type() === 'warning') {
			console.log(`[${JOURNEY}] browser ${msg.type()}: ${msg.text().slice(0, 300)}`);
		}
	});

	await page.reload();
	await page.waitForURL(/\/generate/, { timeout: 15000 });

	// --- Decisive assertions. The viewer element must exist and point at the
	// generation's .glb ...
	const viewer = page.locator('model-viewer');
	await expect(viewer, 'MeshPreview should mount a <model-viewer>').toBeVisible({ timeout: 30000 });
	// Svelte assigns `src` as a DOM property on custom elements, not an
	// attribute - getAttribute() is null here even when the viewer is loading.
	const src = await viewer.evaluate((el: any) => el.src || el.getAttribute('src') || '');
	expect(src, 'viewer src should be the seeded generation .glb').toContain(generationId);

	// ... the mesh must NOT have been handed to the image branch ...
	await expect(page.locator('img[src*=".glb"]')).toHaveCount(0);

	// ... and the model must finish loading: MeshPreview flips opacity to 1 on
	// the load event and drops the spinner. A WebGL/parse failure surfaces as
	// the "Couldn't display this model" alert instead, which fails these.
	await expect(page.getByText("Couldn't display this model")).toHaveCount(0);
	await expect(viewer).toHaveCSS('opacity', '1', { timeout: 30000 });
	await expect(page.getByText('Loading model…')).toHaveCount(0);

	await screenshot(page, JOURNEY, '01-mesh-in-workbench');
	console.log(`[${JOURNEY}] mesh rendered for generation ${generationId}`);
});
