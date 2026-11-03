vec4 effect(vec4 color, vec2 uv) {
    vec3 v = floor(color.rgb * 255.0 + 0.5);
    return vec4(floor(v + u_k * (255.0 - 2.0 * v) + 0.5) / 255.0, color.a);
}
