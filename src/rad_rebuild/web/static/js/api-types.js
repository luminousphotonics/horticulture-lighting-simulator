// @ts-check

/**
 * Frontend API contract typedefs generated from docs/api/radiance-openapi.json.
 * Run `npm run generate:api-types` after checked OpenAPI contract changes.
 *
 * @typedef {object} PublicErrorResponse
 * @property {string} correlation_id
 * @property {Record<string, unknown> | null=} detail
 * @property {string} error
 * @property {string} message
 * @property {false} ok
 *
 * @typedef {object} LayoutResponse
 * @property {Array<[number, number]>} all_positions
 * @property {number} length_ft
 * @property {number} module_count
 * @property {Record<string, number>} module_counts
 * @property {string} png_download_name
 * @property {string} svg
 * @property {string} svg_download_name
 * @property {number} total_cost
 * @property {number} width_ft
 *
 * @typedef {object} RadianceImagesResponse
 * @property {string | null} annot
 * @property {string | null} overlay
 *
 * @typedef {object} RadianceManifestResponse
 * @property {Record<string, unknown>} manifest
 * @property {string} path
 *
 * @typedef {object} RadianceMetricsResponse
 * @property {Record<string, unknown> | null} cost_estimate
 * @property {Record<string, unknown>} metrics
 *
 * @typedef {object} AssemblyRoomResponse
 * @property {number} length_ft
 * @property {number} length_m
 * @property {number} mount_z_m
 * @property {number} width_ft
 * @property {number} width_m
 *
 * @typedef {object} AssemblyAssetsResponse
 * @property {string | null=} anchors
 * @property {string} manifest
 *
 * @typedef {object} AssemblyAxisMappingResponse
 * @property {Array<"x" | "z">} cad_horizontal
 * @property {"y"} cad_vertical
 * @property {Array<"x" | "y">} layout_horizontal
 * @property {"z"} layout_vertical
 *
 * @typedef {object} AssemblyModuleAssetResponse
 * @property {"module_nodes" | null=} anchor_source
 * @property {string | null=} fallback_asset_key
 * @property {string | null} high
 * @property {string | null} medium
 * @property {boolean=} optional
 * @property {string | null} proxy
 *
 * @typedef {object} AssemblyFixturePointResponse
 * @property {number} x
 * @property {number} y
 * @property {number} z
 *
 * @typedef {object} AssemblyAssetFallbackResponse
 * @property {string} asset_key
 * @property {string} fallback_asset_key
 * @property {string[]} instance_ids
 * @property {string} reason
 *
 * @typedef {object} AssemblyInstanceResponse
 * @property {string | null} asset_fallback_key
 * @property {string} asset_key
 * @property {string} id
 * @property {Record<string, unknown> | null=} layout_source
 * @property {string} layout_type
 * @property {number} module_count
 * @property {string | null} orient
 * @property {AssemblyFixturePointResponse[]} points
 * @property {AssemblyFixturePointResponse | null=} position
 * @property {"linear" | "corner" | "centerpiece" | "fixture" | "unknown"} shape
 * @property {string[]} warnings
 * @property {number | null=} yaw_deg
 *
 * @typedef {object} AssemblySceneResponse
 * @property {AssemblyAssetFallbackResponse[]} asset_fallbacks_used
 * @property {AssemblyAssetsResponse} assets
 * @property {AssemblyAxisMappingResponse} axis_mapping
 * @property {string} display_name
 * @property {Record<string, number>} fixture_counts_by_asset_key
 * @property {Record<string, number>} fixture_counts_by_layout_type
 * @property {Record<string, unknown> | null=} fspm_metrics
 * @property {AssemblyInstanceResponse[]} instances
 * @property {string[]} missing_asset_keys
 * @property {"SMD" | "Competitor" | "1000W HPS"} mode
 * @property {string} mode_label
 * @property {Record<string, AssemblyModuleAssetResponse>} module_assets
 * @property {"anchor_fit" | "single_fixture_center"=} placement_strategy
 * @property {Record<string, unknown> | null=} plants
 * @property {AssemblyRoomResponse} room
 * @property {3} schema_version
 * @property {"millimeters"} source_units
 * @property {"proposed_led_system" | "conventional_led_system" | "hps_1000w_system"} system
 * @property {"meters"} units
 * @property {number} viewer_scale
 * @property {string[]} warnings
 *
 * @typedef {object} AssemblyPhotometricBoundsResponse
 * @property {number} x_max
 * @property {number} x_min
 * @property {number} y_max
 * @property {number} y_min
 * @property {number} z_m
 *
 * @typedef {object} AssemblyPhotometricColormapResponse
 * @property {"viridis"} name
 * @property {"linear-clamped"} normalization
 * @property {"matplotlib"} source
 *
 * @typedef {object} AssemblyPhotometricColorScaleResponse
 * @property {true} clamp
 * @property {"simulation-visualization-mean-ppfd"} source
 * @property {number} vmax
 * @property {number} vmin
 *
 * @typedef {object} AssemblyPhotometricOrientationResponse
 * @property {"x"} column_axis
 * @property {"ascending"} column_order
 * @property {"xy"} plane
 * @property {"y"} row_axis
 * @property {"ascending"} row_order
 * @property {"row-major"} storage_order
 * @property {"row * grid_width + column"} value_index
 *
 * @typedef {object} AssemblyPhotometricLayerResponse
 * @property {AssemblyPhotometricBoundsResponse} bounds_m
 * @property {AssemblyPhotometricColorScaleResponse} color_scale
 * @property {AssemblyPhotometricColormapResponse} colormap
 * @property {"float32-le"} encoding
 * @property {number} grid_height
 * @property {number} grid_width
 * @property {number} max_ppfd
 * @property {number} mean_ppfd
 * @property {number} min_ppfd
 * @property {"SMD" | "Competitor" | "1000W HPS"} mode
 * @property {string} mode_label
 * @property {AssemblyPhotometricOrientationResponse} orientation
 * @property {1} schema_version
 * @property {number} target_ppfd
 * @property {"\u00b5mol/m\u00b2/s"} units
 * @property {number} value_count
 * @property {string[]} warnings
 *
 * @typedef {object} RadianceRunResponse
 * @property {string} artifact_token
 * @property {string} job_id
 * @property {string} outdir
 * @property {"queued" | "running" | "succeeded" | "failed" | "cancelling" | "cancelled" | "timed_out"} status
 *
 * @typedef {object} JobTailResponse
 * @property {boolean} done
 * @property {string} job_id
 * @property {string[]} lines
 * @property {number} next_cursor
 * @property {"queued" | "running" | "succeeded" | "failed" | "cancelling" | "cancelled" | "timed_out"} status
 *
 * @typedef {object} RuntimeSetupCommandResponse
 * @property {string} command
 * @property {string} label
 *
 * @typedef {object} RuntimePrecomputedModeStatusResponse
 * @property {true} available
 *
 * @typedef {object} RuntimeDockerModeStatusResponse
 * @property {boolean} available
 * @property {boolean} can_build_image
 * @property {boolean} daemon_available
 * @property {string | null=} docker_cli
 * @property {boolean} image_available
 * @property {string | null=} reason
 * @property {RuntimeSetupCommandResponse[]} setup_commands
 * @property {string[]} supported_lighting_modes
 *
 * @typedef {object} RuntimeLocalModeStatusResponse
 * @property {boolean} available
 * @property {Record<string, string>} detected_executables
 * @property {Record<string, string | null>} env_values
 * @property {string[]} missing_executables
 * @property {string | null=} radiance_bin_dir
 * @property {string | null=} radiance_home
 * @property {string | null=} radiance_lib_dir
 * @property {string | null=} reason
 * @property {RuntimeSetupCommandResponse[]} setup_commands
 * @property {string[]} supported_lighting_modes
 *
 * @typedef {object} RuntimeModesStatusResponse
 * @property {RuntimeDockerModeStatusResponse} live_docker
 * @property {RuntimeLocalModeStatusResponse} live_local
 * @property {RuntimePrecomputedModeStatusResponse} precomputed
 *
 * @typedef {object} RuntimePrivatePhotometryStatusResponse
 * @property {boolean} available
 * @property {boolean} enabled
 * @property {string[]} missing_env_vars
 * @property {Array<"SMD" | "Competitor" | "1000W HPS">} missing_files
 * @property {string | null=} reason
 * @property {Array<"SMD" | "Competitor" | "1000W HPS">} supported_private_modes
 *
 * @typedef {object} RuntimeStatusResponse
 * @property {boolean} live_execution_enabled
 * @property {string[]} live_supported_modes
 * @property {string} live_unsupported_mode_message
 * @property {RuntimeModesStatusResponse} modes
 * @property {RuntimePrivatePhotometryStatusResponse} private_photometry
 *
 * @typedef {object} ElectricalEstimateStageResponse
 * @property {number} avg_ppfd
 * @property {number} days
 * @property {boolean} fixed_output
 * @property {number} hours_per_day
 * @property {string} name
 * @property {number} simulated_mean_ppfd
 * @property {number} stage_cost_usd
 * @property {number} stage_hours
 * @property {number} stage_kwh
 * @property {number} stage_watts
 * @property {unknown} usable_efficacy_umol_j
 * @property {string} watts_basis
 *
 * @typedef {object} ElectricalEstimateResponse
 * @property {number} cycle_cost_usd
 * @property {number} cycle_kwh
 * @property {"SMD" | "Competitor" | "1000W HPS"} mode
 * @property {string[]} notes
 * @property {true} ok
 * @property {ElectricalEstimateStageResponse[]} stages
 * @property {number} utility_rate_kwh
 */

export {};
