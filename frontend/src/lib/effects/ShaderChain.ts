import { outputDuration, sourceToOutput } from '$lib/tool-tabs/video/timeMap';
import type { UniformValue } from './catalog';
import { COMPOSITE, COPY, GEOMETRY, VERTEX, buildFragment } from './glsl';
import type { ChainPass, ChainPlan, EffectPass, Size } from './plan';

export interface ChainFrame {
	mediaTime: number;
	sourceDuration: number;
	fps: number;
}

export interface ChainView {
	compare: boolean;
	split: number;
}

export interface PassTiming {
	tool: string;
	ms: number;
}

export interface ChainHooks {
	failed?: (tool: string) => void;
	lost?: () => void;
	restored?: () => void;
	timing?: (total: number, passes: PassTiming[]) => void;
}

interface Program {
	program: WebGLProgram;
	locations: Map<string, WebGLUniformLocation | null>;
}

interface Target {
	framebuffer: WebGLFramebuffer;
	texture: WebGLTexture;
	size: Size;
}

interface TimerExt {
	TIME_ELAPSED_EXT: number;
	GPU_DISJOINT_EXT: number;
}

interface PendingFrame {
	queries: { tool: string; query: WebGLQuery }[];
}

const MAX_PENDING = 4;

let webgl2: boolean | null = null;

export function supportsWebGL2(): boolean {
	if (webgl2 !== null) return webgl2;
	if (typeof document === 'undefined' || typeof WebGL2RenderingContext === 'undefined') return (webgl2 = false);
	try {
		const gl = document.createElement('canvas').getContext('webgl2');
		webgl2 = !!gl;
		gl?.getExtension('WEBGL_lose_context')?.loseContext();
	} catch {
		webgl2 = false;
	}
	return webgl2;
}

export function frameTime(pass: Pick<EffectPass, 'timeOps'>, frame: ChainFrame): { time: number; duration: number; index: number } {
	const time = sourceToOutput(frame.mediaTime, frame.sourceDuration, pass.timeOps);
	const duration = outputDuration(frame.sourceDuration, pass.timeOps);
	return { time, duration, index: Math.max(0, Math.round(time * frame.fps)) };
}

export class ShaderChain {
	readonly gl: WebGL2RenderingContext;
	private programs = new Map<string, Program | null>();
	private targets = new Map<string, Target>();
	private sources = new Map<string, string>();
	private videoTexture: WebGLTexture | null = null;
	private videoSize: Size = { width: 0, height: 0 };
	private uploaded = false;
	private timer: TimerExt | null;
	private pending: PendingFrame[] = [];
	private lost = false;
	private disposed = false;
	private readonly onLost = (event: Event) => {
		event.preventDefault();
		this.lost = true;
		this.forget();
		this.hooks.lost?.();
	};
	private readonly onRestored = () => {
		this.lost = false;
		this.timer = this.gl.getExtension('EXT_disjoint_timer_query_webgl2') as TimerExt | null;
		this.hooks.restored?.();
	};

	private constructor(
		readonly canvas: HTMLCanvasElement,
		gl: WebGL2RenderingContext,
		private readonly hooks: ChainHooks
	) {
		this.gl = gl;
		this.timer = gl.getExtension('EXT_disjoint_timer_query_webgl2') as TimerExt | null;
		canvas.addEventListener('webglcontextlost', this.onLost);
		canvas.addEventListener('webglcontextrestored', this.onRestored);
	}

	static create(canvas: HTMLCanvasElement, hooks: ChainHooks = {}): ShaderChain | null {
		const gl = canvas.getContext('webgl2', {
			alpha: false,
			antialias: false,
			depth: false,
			stencil: false,
			premultipliedAlpha: false,
			preserveDrawingBuffer: false,
			powerPreference: 'low-power'
		});
		return gl ? new ShaderChain(canvas, gl, hooks) : null;
	}

	get timed(): boolean {
		return !!this.timer;
	}

	get isLost(): boolean {
		return this.lost || this.gl.isContextLost();
	}

	setSource(url: string, text: string) {
		if (this.sources.get(url) === text) return;
		this.sources.set(url, text);
		for (const [key, entry] of [...this.programs.entries()]) {
			if (!key.startsWith(`${url}\n`)) continue;
			if (entry && !this.isLost) this.gl.deleteProgram(entry.program);
			this.programs.delete(key);
		}
	}

	hasSource(url: string): boolean {
		return this.sources.has(url);
	}

	upload(video: HTMLVideoElement): boolean {
		if (this.isLost || video.readyState < 2 || !video.videoWidth) return false;
		const gl = this.gl;
		if (!this.videoTexture) this.videoTexture = this.texture();
		gl.bindTexture(gl.TEXTURE_2D, this.videoTexture);
		gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
		gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
		if (this.videoSize.width === video.videoWidth && this.videoSize.height === video.videoHeight) {
			gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, video);
		} else {
			gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, video);
			this.videoSize = { width: video.videoWidth, height: video.videoHeight };
		}
		this.uploaded = true;
		return true;
	}

	draw(after: ChainPlan, before: ChainPlan | null, frame: ChainFrame, view: ChainView): boolean {
		if (this.isLost || this.disposed || !this.uploaded || !this.videoTexture) return false;
		this.collectTimings();
		const timing = this.timer && this.pending.length < MAX_PENDING ? ({ queries: [] } as PendingFrame) : null;
		const out = this.run('after', after, frame, timing);
		const compare = view.compare && !!before;
		const back = compare && before ? this.run('before', before, frame, null) : null;
		const gl = this.gl;
		if (this.canvas.width !== after.size.width || this.canvas.height !== after.size.height) {
			this.canvas.width = after.size.width;
			this.canvas.height = after.size.height;
		}
		const composite = this.program('composite', COMPOSITE);
		if (!composite) return false;
		gl.bindFramebuffer(gl.FRAMEBUFFER, null);
		gl.viewport(0, 0, this.canvas.width, this.canvas.height);
		gl.useProgram(composite.program);
		gl.activeTexture(gl.TEXTURE0);
		gl.bindTexture(gl.TEXTURE_2D, out);
		gl.activeTexture(gl.TEXTURE1);
		gl.bindTexture(gl.TEXTURE_2D, back ?? out);
		this.set(composite, 'u_input', 'int', 0);
		this.set(composite, 'u_before', 'int', 1);
		this.set(composite, 'u_screen', 'bool', true);
		this.set(composite, 'u_compare', 'bool', !!back);
		this.set(composite, 'u_split', 'float', Math.max(0, Math.min(1, view.split)));
		gl.drawArrays(gl.TRIANGLES, 0, 3);
		gl.activeTexture(gl.TEXTURE0);
		if (timing && timing.queries.length) this.pending.push(timing);
		return true;
	}

	dispose() {
		if (this.disposed) return;
		this.disposed = true;
		this.canvas.removeEventListener('webglcontextlost', this.onLost);
		this.canvas.removeEventListener('webglcontextrestored', this.onRestored);
		if (!this.gl.isContextLost()) {
			const gl = this.gl;
			for (const entry of this.programs.values()) if (entry) gl.deleteProgram(entry.program);
			for (const target of this.targets.values()) {
				gl.deleteFramebuffer(target.framebuffer);
				gl.deleteTexture(target.texture);
			}
			for (const frame of this.pending) for (const entry of frame.queries) gl.deleteQuery(entry.query);
			if (this.videoTexture) gl.deleteTexture(this.videoTexture);
			this.gl.getExtension('WEBGL_lose_context')?.loseContext();
		}
		this.forget();
		this.sources.clear();
		this.canvas.width = 1;
		this.canvas.height = 1;
	}

	private forget() {
		this.programs.clear();
		this.targets.clear();
		this.pending = [];
		this.videoTexture = null;
		this.videoSize = { width: 0, height: 0 };
		this.uploaded = false;
	}

	private run(chain: string, plan: ChainPlan, frame: ChainFrame, timing: PendingFrame | null): WebGLTexture | null {
		const gl = this.gl;
		let input = this.videoTexture;
		plan.passes.forEach((pass, index) => {
			const target = this.target(`${chain}:${index}`, pass.size);
			gl.bindFramebuffer(gl.FRAMEBUFFER, target.framebuffer);
			gl.viewport(0, 0, pass.size.width, pass.size.height);
			gl.activeTexture(gl.TEXTURE0);
			gl.bindTexture(gl.TEXTURE_2D, input);
			const query = timing && pass.kind === 'effect' ? gl.createQuery() : null;
			if (query && timing) {
				gl.beginQuery(this.timer!.TIME_ELAPSED_EXT, query);
				timing.queries.push({ tool: (pass as EffectPass).tool, query });
			}
			this.drawPass(pass, frame);
			if (query) gl.endQuery(this.timer!.TIME_ELAPSED_EXT);
			input = target.texture;
		});
		return input;
	}

	private drawPass(pass: ChainPass, frame: ChainFrame) {
		const gl = this.gl;
		if (pass.kind === 'geometry') {
			const program = this.program('geometry', GEOMETRY);
			if (!program) return;
			gl.useProgram(program.program);
			const [a, b, c, d, e, f] = pass.map;
			this.set(program, 'u_input', 'int', 0);
			this.set(program, 'u_screen', 'bool', false);
			this.set(program, 'u_row0', 'vec3', [a, b, c]);
			this.set(program, 'u_row1', 'vec3', [d, e, f]);
			gl.drawArrays(gl.TRIANGLES, 0, 3);
			return;
		}
		const program = pass.identity ? null : this.effectProgram(pass);
		if (!program) {
			const copy = this.program('copy', COPY);
			if (!copy) return;
			gl.useProgram(copy.program);
			this.set(copy, 'u_input', 'int', 0);
			this.set(copy, 'u_screen', 'bool', false);
			gl.drawArrays(gl.TRIANGLES, 0, 3);
			return;
		}
		const at = frameTime(pass, frame);
		gl.useProgram(program.program);
		this.set(program, 'u_input', 'int', 0);
		this.set(program, 'u_screen', 'bool', false);
		this.set(program, 'u_resolution', 'vec2', [pass.size.width, pass.size.height]);
		this.set(program, 'u_time', 'float', at.time);
		this.set(program, 'u_frame', 'int', at.index);
		this.set(program, 'u_fps', 'float', frame.fps);
		this.set(program, 'u_duration', 'float', at.duration);
		for (const uniform of pass.uniforms) {
			const value = pass.values[uniform.key ?? uniform.name];
			if (value !== undefined) this.set(program, `u_${uniform.name}`, uniform.type, value);
		}
		gl.drawArrays(gl.TRIANGLES, 0, 3);
	}

	private effectProgram(pass: EffectPass): Program | null {
		const source = this.sources.get(pass.shaderUrl);
		if (source === undefined) return null;
		const key = `${pass.shaderUrl}\n${pass.uniforms.map((uniform) => `${uniform.type} ${uniform.name}`).join(',')}`;
		if (this.programs.has(key)) return this.programs.get(key) ?? null;
		let fragment: string;
		try {
			fragment = buildFragment(source, pass.uniforms);
		} catch {
			this.programs.set(key, null);
			this.hooks.failed?.(pass.tool);
			return null;
		}
		const program = this.program(key, fragment);
		if (!program) this.hooks.failed?.(pass.tool);
		return program;
	}

	private program(key: string, fragment: string): Program | null {
		if (this.programs.has(key)) return this.programs.get(key) ?? null;
		const gl = this.gl;
		const vertex = this.shader(gl.VERTEX_SHADER, VERTEX);
		const frag = this.shader(gl.FRAGMENT_SHADER, fragment);
		let entry: Program | null = null;
		if (vertex && frag) {
			const program = gl.createProgram();
			gl.attachShader(program, vertex);
			gl.attachShader(program, frag);
			gl.linkProgram(program);
			if (gl.getProgramParameter(program, gl.LINK_STATUS)) entry = { program, locations: new Map() };
			else gl.deleteProgram(program);
		}
		if (vertex) gl.deleteShader(vertex);
		if (frag) gl.deleteShader(frag);
		this.programs.set(key, entry);
		return entry;
	}

	private shader(type: number, source: string): WebGLShader | null {
		const gl = this.gl;
		const shader = gl.createShader(type);
		if (!shader) return null;
		gl.shaderSource(shader, source);
		gl.compileShader(shader);
		if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
		gl.deleteShader(shader);
		return null;
	}

	private texture(): WebGLTexture {
		const gl = this.gl;
		const texture = gl.createTexture();
		gl.bindTexture(gl.TEXTURE_2D, texture);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
		gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
		return texture;
	}

	private target(key: string, size: Size): Target {
		const existing = this.targets.get(key);
		if (existing && existing.size.width === size.width && existing.size.height === size.height) return existing;
		const gl = this.gl;
		if (existing) {
			gl.deleteFramebuffer(existing.framebuffer);
			gl.deleteTexture(existing.texture);
		}
		const texture = this.texture();
		gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, size.width, size.height);
		const framebuffer = gl.createFramebuffer();
		gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
		gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
		const target = { framebuffer, texture, size: { ...size } };
		this.targets.set(key, target);
		return target;
	}

	private set(program: Program, name: string, type: string, value: UniformValue) {
		const gl = this.gl;
		let location = program.locations.get(name);
		if (location === undefined) {
			location = gl.getUniformLocation(program.program, name);
			program.locations.set(name, location);
		}
		if (!location) return;
		if (type === 'float') gl.uniform1f(location, Number(value));
		else if (type === 'int') gl.uniform1i(location, Math.round(Number(value)));
		else if (type === 'bool') gl.uniform1i(location, value === true || Number(value) !== 0 ? 1 : 0);
		else if (type === 'vec2' && Array.isArray(value)) gl.uniform2f(location, value[0], value[1]);
		else if (type === 'vec3' && Array.isArray(value)) gl.uniform3f(location, value[0], value[1], value[2]);
	}

	private collectTimings() {
		if (!this.timer || !this.pending.length) return;
		const gl = this.gl;
		const disjoint = gl.getParameter(this.timer.GPU_DISJOINT_EXT);
		while (this.pending.length) {
			const frame = this.pending[0];
			if (!frame.queries.every((entry) => gl.getQueryParameter(entry.query, gl.QUERY_RESULT_AVAILABLE))) break;
			this.pending.shift();
			const passes = frame.queries.map((entry) => ({ tool: entry.tool, ms: Number(gl.getQueryParameter(entry.query, gl.QUERY_RESULT)) / 1e6 }));
			for (const entry of frame.queries) gl.deleteQuery(entry.query);
			if (!disjoint) this.hooks.timing?.(passes.reduce((sum, pass) => sum + pass.ms, 0), passes);
		}
	}
}
