// Synthetic UI-only data. All /api traffic is intercepted; no live accounts.
exports.install = async function(context) {
  const state = {selection:null, requests:[], failOverview:false, overview:{enabled:true,m1_reusable:true,
    goals:[
      {id:'g1',title:'每周五天站桩',status:'active',goal_kind:'primary',long_term_direction:'慢慢恢复精力，找回稳定的生活节奏',latest_cycle:{ordinal:2,status:'waiting_execution'},plan:{activity_content:'站桩十分钟',schedule_text:'每周五天',duration_minutes:10,schedule_kind:'recurring',review_cadence:'每周日聊聊感受'}},
      {id:'g2',title:'周末去游泳一次',status:'active',goal_kind:'secondary',long_term_direction:null,latest_cycle:{ordinal:1,status:'planning'},plan:{activity_content:'游泳',schedule_text:'周末',schedule_kind:'one_off',review_cadence:null}},
      {id:'g3',title:'历史散步目标',status:'paused',goal_kind:'unclassified',long_term_direction:null,plan:null,latest_cycle:{ordinal:1,status:'completed'}}],
    activity_records:[{id:'a1',activity_content:'散步',event_kind:'performed',occurred_at_text:'今天',effect:'感觉轻松',goal_id:null},
      {id:'a2',activity_content:'游泳',event_kind:'idea',occurred_at_text:null,effect:null,goal_id:null}]}};
  await context.addInitScript(()=>localStorage.setItem('psy-auth-token','qa-synthetic-token'));
  await context.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname, method=route.request().method();
    state.requests.push({path,method});
    if(path.endsWith('/goal')&&method==='POST')state.selection=route.request().postDataJSON();
    if(path==='/api/conversations'&&method==='POST')state.selection=null;
    const conversation={session_id:'qa-chat',title:'本地验收对话',next_module:state.selection?'module_4':'module_2',messages:[{role:'assistant',content:'我们可以先聊聊你希望改善什么。'}],revision:0,pinned:false,updated_at:'2026-09-16T10:00:00Z'};
    const body=path==='/api/auth/me'?{username:'test',nickname:'本地验收',profile_uuid:'qa',role:'user',email_verified:true,email_required:false,current_module:'module_2',display_id:'test#1000'}:
      path==='/api/program/goals/overview'?state.overview:
      path.startsWith('/api/program/')?{enabled:true,m1_reusable:true,runtime:{current_module:state.selection?'module_4':'module_2',active_goal_id:state.selection?.goal_id??null,row_version:0,flow_status:'active'},goals:state.overview.goals,activity_records:state.overview.activity_records,draft:null,can_confirm:false}:
      path==='/api/conversations'?(method==='POST'?conversation:[conversation]):path.startsWith('/api/conversations/')?conversation:[];
    await route.fulfill({status:path==='/api/program/goals/overview'&&state.failOverview?503:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  return state;
};
