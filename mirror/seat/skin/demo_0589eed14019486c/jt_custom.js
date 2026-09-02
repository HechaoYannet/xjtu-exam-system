(function(){
	let is_demo = true;
	let sectionList = [];
	let error_count = 0;

	window.JTCustom.setConfig("notice", "notice.html");
	window.JTCustom.setTransMsg("zh",{
		"login":{
			"placeholder":"请输入准考证号，字母需要大写"
		}
	})

	function addLoginIntro(){
		//添加在线练习登录页介绍信息
		$("body").append('<div class="addLoginIntro-shadow">'
			+'<div class="addLoginIntro-view">'
			+'<div class="addLoginIntro-ct">'
			+'<div class="section">注意事项：</div>'
			+'<div class="section">请使用以下准考证号登录本机考练习系统，进行机考模拟练习：</div>'
			+'<div class="id-view section">'
			+'<p><span>准考证号：</span><span>1234CS</span></p>'
			+'</div>'
			+'<div class="section" style="color: red">此准考证号仅限在本机考练习系统使用，<strong>正式考试的准考证号请以发布的准考证信息为准。</strong></div>'
			+'<div class="section" style="color: red">机考练习只用于熟悉机考作答环境，使用的<strong>题目并非正式试题，考生无需关注题目内容。</strong></div>'
			+'<div class="section">本模拟考试系统要求计算机浏览器为Edge 104及以上，Chrome 104及以上，Firefox 104及以上，Mac Safari 14及以上或其他对应内核的浏览器。</div>'
			+'<div class="section">打开本模拟考试系统后，请按F11键 开启全屏模式（测试版可以通过最小化等操作切换到计算机桌面，而正式版考试时，考生无法切换屏幕，屏幕会被锁定，这里的F11开启全屏为模拟正式考试场景，优化测试体验）。</div>'
			+'<div class="section">阅读完注意事项后，请点击下方“确定”按钮。</div>'
			+'</div>'
			+'<div class="addLoginIntro-btn-list">'
			+'<button id="addLoginIntro_confirm_btn" class="btn btn-primary pl-4 pr-4 prompt-text-ok" type="button">确 定</button>'
			+'</div>'
			+'</div>'
			+'</div>');
		$("#addLoginIntro_confirm_btn").on("click",function(){
			$(".addLoginIntro-shadow").remove()
		})
	}
	function setLimitWindow(){
		/*
		 * 插入倒计时提醒主体
		 * 功能：倒计时提醒弹出自定义窗口 60秒后关闭
		 * */
		let time_remind_prompt;
		let timer;
		let _form=window.JTCustom.getForm();
		if (_form.sections.length > 1) {
			time_remind_prompt = window.JTCustom.getCurSection().timer.time_remind_prompt;
		} else {
			if (window.JTCustom.getForm().timer.time_remind_prompt && window.JTCustom.getForm().timer.time_remind_prompt!=""){
				time_remind_prompt = window.JTCustom.getForm().timer.time_remind_prompt;
			}else{
				time_remind_prompt = window.JTCustom.getCurSection().timer.time_remind_prompt;
			}

		}

		window.JTCustom.getSession().then(function(e){
			skinName = e.config.skin;
			//添加遮罩
			if($(".limitWindow").length == 0){
				$(".exam-sailfish").append(`
                <div class="limitWindow">
                    <div class="limitWindow_pop">
                        <div class="limitWindow_title">
                            <div class="content_img" style="background-image:url('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAAE3UlEQVRoQ82aW6hUVRjHf/85R1OoSELBErULFnShCB80KY9BaSrM3p7pYlFqdAoiMDKjB8lAAjXJh+hyKqkgS6ez1kRlN1HxlmBRD0XmQ1Z4XtJAIyjyzHyxZ+ZM5z5nn9YcWy/7Ydb+/7/f+vZae39rjQjQbMGCcxg/PgvMxOwiYDKicq20ToxORCeoE7Md8n5/AGs0UhGLoguRLYbMIkiujE2nZb8gbadon6tQ+Czdvf/2Tg1gcTwZSm2gNiiPcoj2FVi7XKE9rdiwARoUeN94U4MMC8DieCbYW8CVaUdohP0/wbhX3v9W7/66ABbHLWC76gmF/13HMJsv748OpT0kgEXRcsSW8MGlUDRmy/svBrtjUACLs6tB61NYNbCrLpNzPw5kMCCAxdkHQK81MKK00mcwpsj7X/ve2A/A4ngh2IdpHUah/y6amhcpn/+zp1cvgMpSyV6wy0choJFYvCfnc0MAZJ8GrR2J8ujdo3vk3NZuv1oGKqNvXwZ6u54AbaNU6gBOkcncD7YyEORhmppnKZ8vJno9AEKOvr0oV3ikO+Dq4BwEpoeB0Co5t6kGYEsXTuCvsd8GGn0oWYsKhT09g7U45ABxnJJdo0LhVDkDFsd3gr0bZnTK49LrOS17RFE74sFgHsYSee8qAEui1zFWBBMXq9XhN/bJwEeg24N5oBfk3KOyXG4speJpzMYFFN8s5x7rBbAk+hrjunAefCfnr5a1RjElktUiXBN5dfg7emcgSt6iE8OZAE3N02Rx/BzY40GFsYNyhRtrq1Bb2xhOnvg7rAeQYbEsirYi7g4s/pOcv6QGkM1OJ6NjgT2S2ftQArAbMTew+Bk5X6uRrbV1NqXigcAeCcBaWRz9AMwILt7UPEn5/InqEppDbA/ugdoTgN+B84KLl+x6FQrfVN4z2ZWg54N7oA8aB6DMQnV07KhmYAPiifAAVgb4viHFutEm71+tZuBt0NLgAMbLySTeibgluHgywVzhmWoGGrFQJA/nmiQDbwL3BQcw9sj7FsvlJlLs6lcKBvEzViQvsmfBngoi2E/ElgPTGlYkZey25BGag9jXGIAGq3YVz69+Tmd/Bk1tsF1o+QNyfk735/RGjFWhHRqrp3Vybk0FIJu9lYw+DWtYWYUslzuXrq6NiIfD6muenNvdoyaOkoL+hjAmekPOJRO41iyOjgMXh9HnkJyflWj1LOqTPf9Xghg0vCbWXXJuWy+AyhszVBa0Sc71mlMWZY8gXRFggLycj7t1+uzMZUNl4ShivTr8Fls2dxynJyxDvBQg+OShuVnO7R0QoJqFj4H5Ycw4mSQ2XClp6+QKa3rG1n9zt3x4p8NgtYoqEMx/lLHNcoVeGwX95kC3g0XRDERS6Pw/mrFP3t80UDCDH3BE0SxEsh141pucHzTOoY+Y4vhSsCPAmLNCYfaDfGHIg8X6h3xRNAnxDjBvVCHE++rwyen/kK0uQHllyuXGU+xKjllb6wmG+V0b5NyTw9EaFkBtcsfx0uo+/8zhiI+gjwcl25K1db6eRiqAajaaKBZXVkGm1DMY5u+HqoGXPw/StNQAtWxksxcgzUNKDsJbgKvSGIN2YrYTab+cG/Gm14gB+gZrudxUrOtaStW/25Cp/t2mBKZOZJ3la5N1ojEHlc//kQ544N7/AHtnvOowpEfNAAAAAElFTkSuQmCC')"></div>
                            <div>提示</div>
                        </div>
                        <div class="limitWindow_content">
                            <div class="content_notice">${time_remind_prompt}</div>
                        </div>
                        <div class="limitWindow_nav">
                            <span>确认</span>
                        </div>
                    </div>
                </div>
            `);
			}
			//绑定事件
			$(".limitWindow_nav").on("click",function(){
				$(".limitWindow").remove();
				clearTimeout(timer);
			});
			//设置60s定时器前，先清除历史定时器
			//定时器真实设置为59s 因为管理机延时多为1min整数，会导致两次弹窗中间没有变化（在考生不进行任何操作时）。
			clearTimeout(timer);
			timer = setTimeout(()=>{
				$(".limitWindow").remove();
				clearTimeout(timer);
			},59000)
		});
	}
	function errorTips(error_text){
		$(".login-check .text-danger").show().html(error_text);
	}
	function btnClickCheck(){
		let _input_val = $(".login-check input").val();

		if(_input_val == ""){
			errorTips("准考证号不能为空");
			return false;
		}

		if(_input_val === "1234CS"){
			$(".login-input input").val("1234CS");
			$("#login-btn").click();
		}else{
			error_count++;
			if(error_count >= 3){
				errorTips("您已连续3次输入错误，请联系监考人员为您核对信息后再次输入。");
			}else{
				errorTips("请输入正确的准考证号");
			}
		}
	}
	function loginCheck(){

		$(".entry-block.sign-in").append(`
			<div _ngcontent-nko-c284="" class="wrap-login-input m-4 login-check">
				<div _ngcontent-qfd-c284="" class="text-danger mx50 ng-star-inserted" style="display: none;"></div>
				<div _ngcontent-nko-c284="" class="login-input d-flex">
					<input _ngcontent-nko-c284="" type="text" class="form-control pl-3"
						   placeholder="请输入准考证号，字母需要大写"/><!---->
					<button _ngcontent-nko-c284="" id="login-btn-check" type="button" class="btn btn-primary ml-2 login-btn"> 登 录</button>
				</div>
			</div>
		`);

		$("#login-btn").off('keydown keypress');
		$("#login-btn-check").on("click",btnClickCheck);
		$("html").on('keydown', function(event) {
			if (event.which === 13 || event.keyCode === 13) {
				if($(".login-check input").is(':focus')){
					event.preventDefault();
					btnClickCheck();
				}
			}
		});
	}
	window.JTCustom.bind("login", () => {
		//每次加载时，重置error次数
		error_count = 0;
		if(is_demo){
			$(".router-login").addClass("is-online-demo");
			addLoginIntro();
			loginCheck();
		}else{
			$(".router-login").removeClass("is-online-demo")
		}
	});
	window.JTCustom.bind("formLoaded", () => {
		let form = window.JTCustom.getForm();

		for(let i = 0;i<form.sections.length;i++){
			sectionList.push({
				isLast : i+1 == form.sections.length?true:false,
				id : form.sections[i].id
			})
		}
	});
	window.JTCustom.bind("timeReminderDisplayed", function () {
		//倒计时提醒--开始
		setLimitWindow();
	});
	window.JTCustom.bind("newSectionDomLoaded", () => {
		let curSection;
		let isLastTag;

		curSection = window.JTCustom.getCurSection();
		for(let i = 0;i<sectionList.length;i++){
			if(curSection.id == sectionList[i].id){
				isLastTag = sectionList[i].isLast
			}
		}

		if(!isLastTag){
			$(".wrap-btn-submit").hide();
		}else{
			$(".wrap-btn-submit").show();
		}
	});
})();