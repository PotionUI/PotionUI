vec4 effect(vec4 color, vec2 uv) {
    vec2 middle = u_resolution * 0.5;
    float d = length(floor(uv * u_resolution) + 0.5 - middle) / length(middle);
    float factor = 1.0 - u_depth * smoothstep(u_inner, u_outer, d);
    vec3 v = floor(color.rgb * 255.0 + 0.5);
    return vec4(floor(v * factor + 0.5) / 255.0, color.a);
}
