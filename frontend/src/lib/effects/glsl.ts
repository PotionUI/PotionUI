import type { EffectUniform } from './catalog';

export const MAX_SHADER_BYTES = 64 * 1024;

const HEADER = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler3D;
`;

const BUILTINS = `uniform sampler2D u_input;
uniform vec2 u_resolution;
uniform float u_time;
uniform int u_frame;
uniform float u_fps;
uniform float u_duration;
`;

const BUILTIN_NAMES = new Set(['input', 'resolution', 'time', 'frame', 'fps', 'duration']);

export const VERTEX = `#version 300 es
uniform bool u_screen;
out vec2 v_uv;
void main() {
    vec2 position = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    v_uv = position;
    if (u_screen) v_uv.y = 1.0 - v_uv.y;
    gl_Position = vec4(position * 2.0 - 1.0, 0.0, 1.0);
}
`;

export const COPY = `${HEADER}uniform sampler2D u_input;
in vec2 v_uv;
out vec4 o_color;
void main() {
    o_color = texture(u_input, v_uv);
}
`;

export const GEOMETRY = `${HEADER}uniform sampler2D u_input;
uniform vec3 u_row0;
uniform vec3 u_row1;
in vec2 v_uv;
out vec4 o_color;
void main() {
    vec3 p = vec3(v_uv, 1.0);
    vec2 uv = vec2(dot(u_row0, p), dot(u_row1, p));
    o_color = texture(u_input, uv);
}
`;

export const COMPOSITE = `${HEADER}uniform sampler2D u_input;
uniform sampler2D u_before;
uniform bool u_compare;
uniform float u_split;
in vec2 v_uv;
out vec4 o_color;
void main() {
    vec4 after = texture(u_input, v_uv);
    o_color = u_compare && v_uv.x < u_split ? texture(u_before, v_uv) : after;
    o_color.a = 1.0;
}
`;

const FORBIDDEN = /#\s*(version|extension|include|line)\b|\buniform\s+\w/;

export class ShaderRejected extends Error {}

export function checkEffectSource(source: string): string {
	if (new TextEncoder().encode(source).length > MAX_SHADER_BYTES) throw new ShaderRejected('The shader is too large');
	if (FORBIDDEN.test(source)) throw new ShaderRejected('The shader declares what the host owns');
	if (!/\bvec4\s+effect\s*\(/.test(source)) throw new ShaderRejected('The shader has no effect() function');
	return source;
}

export function buildFragment(source: string, uniforms: readonly EffectUniform[], textures: readonly string[] = []): string {
	const declared = uniforms
		.filter((uniform) => !BUILTIN_NAMES.has(uniform.name))
		.map((uniform) => `uniform ${uniform.type} u_${uniform.name};`)
		.join('\n');
	const samplers = textures.map((name) => `uniform highp sampler3D u_${name};\nuniform int u_${name}_size;`).join('\n');
	return `${HEADER}
${BUILTINS}
${declared}
${samplers}

in vec2 v_uv;
out vec4 o_color;

vec4 sampleInput(vec2 uv) {
    return texture(u_input, uv);
}

${checkEffectSource(source)}

void main() {
    o_color = effect(texture(u_input, v_uv), v_uv);
}
`;
}
