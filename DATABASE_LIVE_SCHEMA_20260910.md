# 线上数据库结构快照 · 2026-09-10

只读读取 information_schema；不包含用户正文、密码或令牌。数据库：`ba_coach_260908`。

此文是实际字段清单，不是 V2 目标设计。完整默认值、索引和外键仍须查 SHOW CREATE TABLE；本次快照列出类型和可空性。

## account_email_tokens

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

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `account_id` | `int` | NO |
| `email` | `varchar(320)` | NO |
| `normalized_email` | `varchar(320)` | NO |
| `verified_at` | `datetime` | YES |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |

## account_handles

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `account_id` | `int` | NO |
| `base_username` | `varchar(58)` | NO |
| `normalized_base` | `varchar(58)` | NO |
| `tag` | `varchar(5)` | NO |
| `created_at` | `datetime` | NO |

## account_settings

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `account_id` | `int` | NO |
| `preferred_provider` | `varchar(16)` | NO |
| `role` | `varchar(16)` | NO |
| `updated_at` | `datetime` | NO |

## activity_logs

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

## ai_execution_events

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

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `account_id` | `int` | NO |
| `token_hash` | `varchar(64)` | NO |
| `created_at` | `datetime` | NO |
| `expires_at` | `datetime` | NO |

## clinical_record_cycle_links

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `cycle_id` | `varchar(36)` | NO |
| `module` | `varchar(16)` | NO |
| `record_id` | `varchar(36)` | NO |
| `created_at` | `datetime` | NO |

## conversation_messages

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

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `conversation_id` | `int` | NO |
| `module` | `varchar(16)` | YES |
| `memory` | `json` | NO |
| `updated_at` | `datetime` | NO |

## conversations

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

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `run_id` | `varchar(36)` | NO |
| `reviewer_id` | `int` | NO |
| `verdict` | `varchar(16)` | NO |
| `notes` | `text` | NO |
| `created_at` | `datetime` | NO |

## evaluation_runs

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

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `source_id` | `int` | NO |
| `ordinal` | `int` | NO |
| `heading` | `varchar(512)` | NO |
| `content` | `longtext` | NO |

## knowledge_sources

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `name` | `varchar(255)` | NO |
| `category` | `varchar(8)` | NO |
| `content_hash` | `varchar(64)` | NO |
| `updated_by` | `varchar(64)` | NO |
| `created_at` | `datetime` | NO |
| `updated_at` | `datetime` | NO |

## module_four_record

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

## module_one_record

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

## module_three_record

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

## module_two_record

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

## pa_cycles

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `varchar(36)` | NO |
| `conversation_id` | `int` | NO |
| `subject_id` | `varchar(64)` | NO |
| `ordinal` | `int` | NO |
| `status` | `varchar(16)` | NO |
| `started_at` | `datetime` | NO |
| `closed_at` | `datetime` | YES |

## profile_extensions

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

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `prompt_key` | `varchar(32)` | NO |
| `content` | `longtext` | NO |
| `updated_by` | `varchar(64)` | NO |
| `updated_at` | `datetime` | NO |

## risk_monitoring

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

| 字段 | MySQL 类型 | 可空 |
|---|---|---|
| `id` | `int` | NO |
| `username` | `varchar(64)` | NO |
| `password_hash` | `varchar(255)` | NO |
| `profile_uuid` | `varchar(36)` | NO |
| `created_at` | `datetime` | NO |
| `last_login_at` | `datetime` | YES |

## user_profile

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
