# 生产 V2 数据库实际字段快照

库：`ba_coach_260908`。采集于 2026-09-10 生产切换后；来自 information_schema，仅包含结构和行数，不含用户正文或凭据。

`legacy_20260910t155426z_*` 是切换前归档，不是当前业务模型。`profile_extensions`、`conversation_module_progress`、`clinical_record_cycle_links` 为保留的旧侧表，V2 业务读写不再以它们为权威来源。

## account_email_tokens

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `account_id` | `int` | NO |
| `purpose` | `varchar(24)` | NO |
| `token_hash` | `varchar(64)` | NO |
| `created_at` | `datetime` | NO |
| `expires_at` | `datetime` | NO |
| `consumed_at` | `datetime` | YES |

## account_emails

采集时行数：12。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `account_id` | `int` | NO |
| `email` | `varchar(320)` | NO |
| `normalized_email` | `varchar(320)` | NO |
| `verified_at` | `datetime` | YES |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |

## account_handles

采集时行数：16。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `account_id` | `int` | NO |
| `base_username` | `varchar(58)` | NO |
| `normalized_base` | `varchar(58)` | NO |
| `tag` | `varchar(5)` | NO |
| `created_at` | `datetime` | NO |

## account_settings

采集时行数：16。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `account_id` | `int` | NO |
| `preferred_provider` | `varchar(16)` | NO |
| `role` | `varchar(16)` | NO |
| `updated_at` | `datetime` | NO |

## activity_logs

采集时行数：1。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `entry_id` | `int` | NO |
| `position` | `int` | NO |
| `time_slot` | `varchar(64)` | NO |
| `activity` | `varchar(500)` | NO |
| `emotion` | `int` | NO |
| `achievement` | `int` | NO |
| `connection` | `int` | NO |
| `enjoyment` | `int` | NO |
| `importance` | `int` | NO |
| `note` | `text` | YES |

## ai_decision_logs

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `conversation_id` | `int` | YES |
| `turn_id` | `varchar(64)` | NO |
| `goal_id` | `varchar(36)` | YES |
| `cycle_id` | `varchar(36)` | YES |
| `module_name` | `varchar(16)` | NO |
| `decision_type` | `varchar(32)` | NO |
| `decision_value` | `json` | NO |
| `reason_summary` | `varchar(1000)` | YES |
| `evidence_message_ids` | `json` | YES |
| `schema_version` | `smallint` | NO |
| `created_at` | `datetime(6)` | NO |

## ai_execution_events

采集时行数：135。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `conversation_id` | `int` | YES |
| `assistant_message_id` | `int` | YES |
| `subject_id` | `varchar(64)` | YES |
| `session_id` | `varchar(64)` | YES |
| `stage` | `varchar(32)` | NO |
| `provider` | `varchar(32)` | YES |
| `model_name` | `varchar(128)` | YES |
| `duration_ms` | `int` | YES |
| `input_tokens` | `int` | YES |
| `output_tokens` | `int` | YES |
| `reasoning_tokens` | `int` | YES |
| `provider_request_id` | `varchar(128)` | YES |
| `finish_reason` | `varchar(32)` | YES |
| `error_code` | `varchar(128)` | YES |
| `prompt_version` | `varchar(64)` | YES |
| `estimated_cost_usd` | `decimal(14,8)` | YES |
| `pricing_version` | `varchar(64)` | YES |
| `event_metadata` | `json` | YES |
| `created_at` | `datetime` | NO |

## assessment_entries

采集时行数：1。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `subject_id` | `varchar(64)` | NO |
| `recorded_on` | `date` | NO |
| `timezone` | `varchar(64)` | NO |
| `status` | `varchar(16)` | NO |
| `completion_rate` | `int` | YES |
| `activity_level` | `int` | YES |
| `social_connection` | `int` | YES |
| `approach_vs_avoidance` | `int` | YES |
| `overall_mood` | `int` | YES |
| `reflection_note` | `text` | YES |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |

## auth_sessions

采集时行数：32。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `account_id` | `int` | NO |
| `token_hash` | `varchar(64)` | NO |
| `created_at` | `datetime` | NO |
| `expires_at` | `datetime` | NO |

## ba_memory

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `user_id` | `varchar(36)` | NO |
| `memory_type` | `varchar(32)` | NO |
| `memory_key` | `varchar(128)` | NO |
| `content` | `text` | NO |
| `source_kind` | `varchar(24)` | NO |
| `source_message_id` | `int` | YES |
| `confirmation_status` | `varchar(24)` | NO |
| `confidence_level` | `smallint` | YES |
| `status` | `varchar(24)` | NO |
| `supersedes_id` | `varchar(36)` | YES |
| `valid_from` | `datetime(6)` | YES |
| `valid_until` | `datetime(6)` | YES |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |

## clinical_record_cycle_links

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `cycle_id` | `varchar(36)` | NO |
| `module` | `varchar(16)` | NO |
| `record_id` | `varchar(36)` | NO |
| `created_at` | `datetime` | NO |

## conversation_messages

采集时行数：688。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `conversation_id` | `int` | NO |
| `position` | `int` | NO |
| `role` | `varchar(16)` | NO |
| `content` | `text` | NO |
| `reasoning_content` | `longtext` | YES |
| `model_name` | `varchar(128)` | YES |
| `routing_reasoning_content` | `longtext` | YES |
| `router_model_name` | `varchar(128)` | YES |
| `risk_gate_duration_ms` | `int` | YES |
| `time_to_first_reasoning_token_ms` | `int` | YES |
| `time_to_first_content_token_ms` | `int` | YES |
| `main_generation_duration_ms` | `int` | YES |
| `router_duration_ms` | `int` | YES |
| `input_tokens` | `int` | YES |
| `output_tokens` | `int` | YES |
| `reasoning_tokens` | `int` | YES |
| `provider_request_id` | `varchar(128)` | YES |
| `finish_reason` | `varchar(32)` | YES |
| `error_code` | `varchar(64)` | YES |
| `prompt_version` | `varchar(64)` | YES |
| `created_at` | `datetime` | NO |

## conversation_module_progress

采集时行数：4。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `conversation_id` | `int` | NO |
| `module_1_steps` | `json` | NO |
| `module_2_steps` | `json` | NO |
| `module_3_steps` | `json` | NO |
| `module_4_steps` | `json` | NO |
| `active_cycle_id` | `varchar(36)` | YES |
| `updated_at` | `datetime` | NO |

## conversation_runtime_states

采集时行数：34。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `conversation_id` | `int` | NO |
| `current_module` | `varchar(24)` | NO |
| `flow_status` | `varchar(24)` | NO |
| `active_goal_id` | `varchar(36)` | YES |
| `active_cycle_id` | `varchar(36)` | YES |
| `last_transition_reason` | `varchar(255)` | YES |
| `row_version` | `int` | NO |
| `memory` | `json` | NO |
| `updated_at` | `datetime(6)` | NO |

## conversations

采集时行数：34。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `subject_id` | `varchar(64)` | NO |
| `session_id` | `varchar(64)` | NO |
| `title` | `varchar(80)` | NO |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |
| `pinned` | `tinyint(1)` | NO |
| `revision` | `int` | NO |

## evaluation_cases

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `case_code` | `varchar(64)` | NO |
| `module` | `varchar(16)` | NO |
| `user_type` | `text` | NO |
| `scenario` | `text` | NO |
| `user_input` | `text` | NO |
| `expected_behavior` | `text` | NO |
| `extra_columns` | `json` | NO |
| `revision` | `int` | NO |
| `created_by` | `int` | NO |
| `updated_at` | `datetime` | NO |

## evaluation_reviews

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `run_id` | `varchar(36)` | NO |
| `reviewer_id` | `int` | NO |
| `verdict` | `varchar(16)` | NO |
| `notes` | `text` | NO |
| `created_at` | `datetime` | NO |

## evaluation_runs

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `case_id` | `varchar(36)` | NO |
| `parent_run_id` | `varchar(36)` | YES |
| `created_by` | `int` | NO |
| `status` | `varchar(16)` | NO |
| `case_snapshot` | `json` | NO |
| `prompt_snapshot` | `json` | NO |
| `provider` | `varchar(32)` | NO |
| `model` | `varchar(128)` | YES |
| `input_text` | `text` | NO |
| `reply` | `text` | NO |
| `transcript` | `json` | NO |
| `memory` | `json` | NO |
| `metrics` | `json` | NO |
| `error_code` | `varchar(80)` | YES |
| `created_at` | `datetime` | NO |
| `finished_at` | `datetime` | YES |

## interaction_status

采集时行数：4。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `char(36)` | NO |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |
| `user_id` | `char(36)` | NO |
| `last_active_at` | `datetime` | YES |
| `consecutive_inactive_days` | `int` | NO |
| `total_interaction_count` | `int` | NO |
| `avg_weekly_interaction_frequency` | `decimal(8,2)` | YES |
| `good_response_behaviors` | `json` | YES |
| `easy_stuck_modules` | `json` | YES |
| `decline_behavior_signals` | `json` | YES |
| `decline_somatic_signals` | `json` | YES |
| `decline_cognitive_signals` | `json` | YES |
| `full_m2_m3_m4_cycle_count` | `int` | NO |
| `longest_stay_module` | `varchar(64)` | YES |
| `goal_history` | `json` | YES |
| `behavior_activation_level_change` | `json` | YES |
| `overall_emotion_trend` | `varchar(255)` | YES |
| `has_entered_closure_or_transition` | `tinyint` | NO |
| `self_coaching_confidence_level` | `tinyint` | YES |
| `reaction_to_ai_coach_ending` | `text` | YES |

## issue_reports

采集时行数：3。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `account_id` | `int` | NO |
| `subject_id` | `varchar(64)` | NO |
| `description` | `text` | NO |
| `status` | `varchar(16)` | NO |
| `screenshot` | `mediumblob` | YES |
| `screenshot_mime` | `varchar(32)` | YES |
| `page_url` | `varchar(2048)` | NO |
| `session_id` | `varchar(64)` | YES |
| `last_error` | `text` | YES |
| `user_agent` | `varchar(512)` | NO |
| `viewport_width` | `int` | YES |
| `viewport_height` | `int` | YES |
| `client_online` | `tinyint(1)` | YES |
| `created_at` | `datetime` | NO |
| `resolved_at` | `datetime` | YES |

## knowledge_chunks

采集时行数：1331。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `source_id` | `int` | NO |
| `ordinal` | `int` | NO |
| `heading` | `varchar(512)` | NO |
| `content` | `longtext` | NO |

## knowledge_sources

采集时行数：10。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `name` | `varchar(255)` | NO |
| `category` | `varchar(8)` | NO |
| `content_hash` | `varchar(64)` | NO |
| `updated_by` | `varchar(64)` | NO |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |

## legacy_20260910t155426z_conversation_runtime_states

采集时行数：32。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `conversation_id` | `int` | NO |
| `module` | `varchar(16)` | YES |
| `memory` | `json` | NO |
| `updated_at` | `datetime` | NO |

## legacy_20260910t155426z_module_four_record

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `char(36)` | NO |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |
| `user_id` | `char(36)` | NO |
| `execution_result` | `tinyint` | YES |
| `phase_a` | `json` | YES |
| `phase_b` | `json` | YES |
| `phase_c` | `json` | YES |
| `ai_abc_chain_summary` | `text` | YES |
| `user_chain_approval_level` | `tinyint` | YES |
| `core_difficulty_type` | `varchar(128)` | YES |
| `difficulty_description` | `text` | YES |
| `ba_reeducation_content` | `text` | YES |
| `next_coping_strategy` | `text` | YES |
| `review_decision` | `tinyint` | YES |
| `review_summary` | `text` | YES |

## legacy_20260910t155426z_module_one_record

采集时行数：4。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `char(36)` | NO |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |
| `user_id` | `char(36)` | NO |
| `chief_complaint` | `text` | YES |
| `distress_duration` | `varchar(64)` | YES |
| `distress_frequency` | `varchar(64)` | YES |
| `trigger_situation` | `text` | YES |
| `abc_event` | `json` | YES |
| `coping_behavior` | `text` | YES |
| `coping_consequence` | `text` | YES |
| `ai_depression_cycle_summary` | `text` | YES |
| `user_approval_level` | `tinyint` | YES |
| `attempted_relief_methods` | `json` | YES |
| `exception_positive_scene` | `json` | YES |

## legacy_20260910t155426z_module_three_record

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `char(36)` | NO |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |
| `user_id` | `char(36)` | NO |
| `ai_record_requirement` | `text` | YES |
| `user_acceptance_level` | `tinyint` | YES |
| `user_acceptance_feeling` | `text` | YES |
| `negotiated_record_plan` | `text` | YES |
| `has_contract_reached` | `tinyint` | NO |
| `difficulty_feedback_mechanism` | `text` | YES |

## legacy_20260910t155426z_module_two_record

采集时行数：1。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `char(36)` | NO |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |
| `user_id` | `char(36)` | NO |
| `pa_understanding_level` | `tinyint` | YES |
| `pa_approval_level` | `tinyint` | YES |
| `core_values` | `text` | YES |
| `core_values_impact` | `text` | YES |
| `target_activity_content` | `varchar(255)` | YES |
| `target_activity_time` | `datetime` | YES |
| `target_activity_location` | `varchar(255)` | YES |
| `target_activity_duration_minutes` | `int` | YES |
| `target_activity_companion` | `varchar(32)` | YES |
| `potential_barriers` | `json` | YES |
| `barrier_coping_plan` | `json` | YES |
| `has_target_card_generated` | `tinyint` | NO |

## legacy_20260910t155426z_pa_cycles

采集时行数：2。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `conversation_id` | `int` | NO |
| `subject_id` | `varchar(64)` | NO |
| `ordinal` | `int` | NO |
| `status` | `varchar(16)` | NO |
| `started_at` | `datetime` | NO |
| `closed_at` | `datetime` | YES |

## legacy_20260910t155426z_user_profile

采集时行数：23。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `bigint unsigned` | NO |
| `uuid` | `char(36)` | NO |
| `nickname` | `varchar(64)` | YES |
| `age` | `tinyint unsigned` | YES |
| `living_status` | `enum('独居','和家人','和朋友','和恋人')` | YES |
| `has_supporter` | `tinyint(1)` | NO |
| `supporter1_relation` | `enum('父母','恋人','子女','朋友','兄弟姐妹','同事')` | YES |
| `supporter1_nickname` | `varchar(64)` | YES |
| `supporter1_influence` | `enum('弱','中','强')` | YES |
| `supporter2_relation` | `enum('父母','恋人','子女','朋友','兄弟姐妹','同事')` | YES |
| `supporter2_nickname` | `varchar(64)` | YES |
| `supporter2_influence` | `enum('弱','中','强')` | YES |
| `risk_level` | `enum('低','中','高')` | YES |
| `current_module` | `enum('开场','模块一','模块二','模块三','模块四')` | YES |
| `module_completion_flag` | `binary(4)` | YES |
| `module_task_completion` | `json` | YES |
| `module1_done_flag` | `tinyint(1)` | NO |
| `communication_preference` | `enum('直接明了','温柔引导','理性分析','轻松幽默')` | YES |
| `reminder_frequency` | `enum('每天一次','隔天一次','每三天一次','每周一次','仅在我主动找你时提醒','暂时不需要提醒')` | YES |
| `reminder_time_slot` | `enum('早晨7-9','上午9-12','中午12-14','下午14-18','傍晚18-21','晚上21-23')` | YES |
| `physical_condition` | `set('膝关节损伤','腰背酸痛','慢性疼痛','易疲劳','睡眠障碍','偏头痛','哮喘','眩晕','鼻炎','术后恢复期')` | YES |
| `behavior_taboo` | `set('不能剧烈运动','不能久站','不能晒太阳','怕吵闹','怕人多','怕拥挤闭塞的地方','不坐公共交通')` | YES |
| `content_taboo` | `set('不谈工作','不谈学习','不谈家庭','不谈身材外貌','不谈感情','不谈未来计划','不喜欢被比较','反感正能量说教','不喜欢被经常催促')` | YES |
| `expression_style` | `enum('理性','情绪化','回避','混合')` | YES |
| `activity_environment` | `enum('室内','户外','都可以')` | YES |
| `activity_social` | `enum('独自','一对一','群体','都可以')` | YES |
| `activity_intensity` | `enum('安静','热闹','都可以')` | YES |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |

## module_four_record

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `cycle_id` | `varchar(36)` | NO |
| `record_status` | `varchar(24)` | NO |
| `scenario_type` | `varchar(8)` | YES |
| `execution_result` | `smallint` | YES |
| `phase_a` | `json` | YES |
| `phase_b` | `json` | YES |
| `phase_c` | `json` | YES |
| `abc_chain_summary` | `text` | YES |
| `chain_confirmation_status` | `varchar(24)` | NO |
| `confirmation_message_id` | `int` | YES |
| `core_difficulty_type` | `varchar(128)` | YES |
| `difficulty_description` | `text` | YES |
| `ba_reeducation_content` | `text` | YES |
| `next_coping_strategy` | `text` | YES |
| `review_decision` | `smallint` | YES |
| `review_summary` | `text` | YES |
| `confirmed_at` | `datetime(6)` | YES |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |

## module_one_record

采集时行数：4。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `user_id` | `varchar(36)` | NO |
| `version_no` | `int` | NO |
| `record_status` | `varchar(24)` | NO |
| `chief_complaint` | `text` | YES |
| `distress_duration` | `varchar(128)` | YES |
| `distress_frequency` | `varchar(128)` | YES |
| `trigger_situation` | `text` | YES |
| `event_experience` | `json` | YES |
| `coping_behavior` | `text` | YES |
| `coping_consequence` | `text` | YES |
| `functional_chain_summary` | `text` | YES |
| `confirmation_status` | `varchar(24)` | NO |
| `confirmation_message_id` | `int` | YES |
| `ba_explanation_status` | `varchar(24)` | NO |
| `ba_explanation_message_id` | `int` | YES |
| `goal_setting_willingness` | `varchar(24)` | NO |
| `willingness_message_id` | `int` | YES |
| `attempted_relief_methods` | `json` | YES |
| `exception_positive_scene` | `json` | YES |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |

## module_three_record

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `goal_id` | `varchar(36)` | NO |
| `module_two_record_id` | `varchar(36)` | NO |
| `version_no` | `int` | NO |
| `record_status` | `varchar(24)` | NO |
| `record_requirement` | `text` | YES |
| `acceptance_status` | `varchar(24)` | YES |
| `acceptance_feeling` | `text` | YES |
| `negotiated_record_plan` | `json` | YES |
| `feedback_mechanism` | `text` | YES |
| `reminder_enabled` | `tinyint(1)` | NO |
| `reminder_rule` | `json` | YES |
| `reminder_text` | `varchar(255)` | YES |
| `confirmation_message_id` | `int` | YES |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |

## module_two_record

采集时行数：1。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `goal_id` | `varchar(36)` | NO |
| `version_no` | `int` | NO |
| `record_status` | `varchar(24)` | NO |
| `pa_understanding_status` | `varchar(24)` | YES |
| `pa_willingness_status` | `varchar(24)` | YES |
| `core_values` | `json` | YES |
| `core_values_impact` | `text` | YES |
| `activity_content` | `text` | YES |
| `schedule_text` | `varchar(255)` | YES |
| `scheduled_start_at` | `datetime(6)` | YES |
| `timezone` | `varchar(64)` | NO |
| `location` | `varchar(255)` | YES |
| `duration_minutes` | `smallint` | YES |
| `frequency_rule` | `json` | YES |
| `companion` | `varchar(255)` | YES |
| `potential_barriers` | `json` | YES |
| `barrier_coping_plan` | `json` | YES |
| `confirmation_status` | `varchar(24)` | NO |
| `confirmation_message_id` | `int` | YES |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |

## pa_cycle_progress

采集时行数：2。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `cycle_id` | `varchar(36)` | NO |
| `module_2_steps` | `json` | NO |
| `module_3_steps` | `json` | NO |
| `module_4_steps` | `json` | NO |
| `module_4_scenario` | `varchar(8)` | YES |
| `row_version` | `int` | NO |
| `updated_at` | `datetime(6)` | NO |

## pa_cycles

采集时行数：2。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `goal_id` | `varchar(36)` | NO |
| `ordinal` | `int` | NO |
| `module_two_record_id` | `varchar(36)` | YES |
| `module_three_record_id` | `varchar(36)` | YES |
| `status` | `varchar(24)` | NO |
| `started_from_conversation_id` | `int` | YES |
| `planned_for_at` | `datetime(6)` | YES |
| `started_at` | `datetime(6)` | YES |
| `completed_at` | `datetime(6)` | YES |
| `cancel_reason` | `varchar(255)` | YES |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |

## pa_goals

采集时行数：3。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `user_id` | `varchar(36)` | NO |
| `title` | `varchar(255)` | NO |
| `status` | `varchar(24)` | NO |
| `module_one_record_id` | `varchar(36)` | YES |
| `current_plan_record_id` | `varchar(36)` | YES |
| `replaced_by_goal_id` | `varchar(36)` | YES |
| `status_reason` | `varchar(255)` | YES |
| `created_from_conversation_id` | `int` | YES |
| `closed_at` | `datetime(6)` | YES |
| `row_version` | `int` | NO |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |

## profile_extensions

采集时行数：0。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `profile_uuid` | `varchar(36)` | NO |
| `reminder_start_minute` | `int` | YES |
| `reminder_end_minute` | `int` | YES |
| `supporters` | `json` | YES |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |

## prompt_overrides

采集时行数：6。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `prompt_key` | `varchar(32)` | NO |
| `content` | `longtext` | NO |
| `updated_by` | `varchar(64)` | NO |
| `updated_at` | `datetime` | NO |

## risk_monitoring

采集时行数：2。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `char(36)` | NO |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |
| `user_id` | `char(36)` | NO |
| `risk_status` | `tinyint` | NO |
| `risk_expression_type` | `tinyint` | YES |
| `risk_expression_time` | `datetime` | YES |
| `risk_context` | `json` | YES |
| `user_reaction_to_risk` | `varchar(255)` | YES |
| `ai_intervention_record` | `json` | YES |

## user_accounts

采集时行数：16。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `username` | `varchar(64)` | NO |
| `password_hash` | `varchar(255)` | NO |
| `profile_uuid` | `varchar(36)` | NO |
| `created_at` | `datetime` | NO |
| `last_login_at` | `datetime` | YES |

## user_activity_constraints

采集时行数：50。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `user_id` | `varchar(36)` | NO |
| `constraint_type` | `varchar(24)` | NO |
| `category` | `varchar(32)` | NO |
| `content` | `text` | NO |
| `source_type` | `varchar(24)` | NO |
| `source_message_id` | `int` | YES |
| `confirmation_status` | `varchar(24)` | NO |
| `status` | `varchar(24)` | NO |
| `supersedes_id` | `varchar(36)` | YES |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |

## user_module_one_state

采集时行数：23。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `user_id` | `varchar(36)` | NO |
| `completed_steps` | `json` | NO |
| `confirmed_formulation_id` | `varchar(36)` | YES |
| `status` | `varchar(24)` | NO |
| `completion_source` | `varchar(24)` | NO |
| `evidence_status` | `varchar(24)` | NO |
| `row_version` | `int` | NO |
| `updated_at` | `datetime(6)` | NO |

## user_preferences

采集时行数：23。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `user_id` | `varchar(36)` | NO |
| `communication_style` | `varchar(32)` | YES |
| `response_verbosity` | `varchar(16)` | YES |
| `activity_environment` | `varchar(16)` | YES |
| `activity_social` | `varchar(16)` | YES |
| `activity_atmosphere` | `varchar(16)` | YES |
| `preferred_activity_intensity` | `varchar(16)` | YES |
| `reminder_frequency` | `varchar(64)` | YES |
| `reminder_time` | `varchar(5)` | YES |
| `reminder_start_minute` | `smallint` | YES |
| `reminder_end_minute` | `smallint` | YES |
| `reminder_time_slot` | `varchar(32)` | YES |
| `updated_at` | `datetime(6)` | NO |

## user_profile

采集时行数：23。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `uuid` | `varchar(36)` | NO |
| `nickname` | `varchar(64)` | YES |
| `birth_year` | `smallint` | YES |
| `reported_age` | `smallint` | YES |
| `age_reported_at` | `datetime(6)` | YES |
| `gender` | `varchar(32)` | YES |
| `occupation_status` | `varchar(32)` | YES |
| `living_status` | `varchar(32)` | YES |
| `timezone` | `varchar(64)` | NO |
| `module1_done_flag` | `tinyint(1)` | NO |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |

## user_supporters

采集时行数：2。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `user_id` | `varchar(36)` | NO |
| `position` | `int` | NO |
| `relation` | `varchar(32)` | YES |
| `nickname` | `varchar(64)` | YES |
| `influence` | `varchar(8)` | YES |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |

## v2_migration_issues

采集时行数：36。

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `source_table` | `varchar(64)` | NO |
| `source_id` | `varchar(64)` | NO |
| `issue_code` | `varchar(64)` | NO |
| `resolution` | `varchar(255)` | YES |
| `status` | `varchar(24)` | NO |
| `created_at` | `datetime(6)` | NO |
| `updated_at` | `datetime(6)` | NO |
