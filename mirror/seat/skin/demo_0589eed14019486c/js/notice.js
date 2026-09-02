/**
 * Created by 马天野 on 2020/2/24.
 */
class noticeClass {
    init() {
        let _AutoTest = false;
        function autoTest() {
            setTimeout(() => {
                _AutoTest = window.parent.JTCustom.isAutoTest();
                if (window.parent.JTCustom.isFormAvailable()) {
                    if (_AutoTest) {
                        window.parent.JTCustom.startExam();
                    } else {
                        autoTest();
                    }
                } else {
                    autoTest();
                }
            }, 3000)
        }
        autoTest();
        this.bindFun();
        this.getNotice();
    }
    bindExamNoticeBtn(){
        let _this = this;

        $(".exam-rules-agree").bind("click",function(){
            $(".exam-rules").hide();
            $(".operation-intro").show();
            window.parent.JTCustom.getSession().then(function(e){
                //操作指南页绑定开考监听
                if(e.status == "open") {
                    window.parent.JTCustom.unbind("examStart");
                    window.parent.JTCustom.bind("examStart", function() {
                        canStartExam1();
                        function canStartExam1(cbfun){
                            if (window.parent) {
                                if(window.parent.JTCustom.isFormAvailable()){
                                    typeof cbfun === "function" ? cbfun() : "";
                                    window.parent.JTCustom.startExam();
                                }else {
                                    $(".tip").text("数据加载中，请稍后重试。");
                                }
                            }
                        }
                        let timer1 = setInterval(() => {
                            canStartExam1(function(){
                                clearInterval(timer1)
                            })
                        }, 1000);
                    });
                }
                if(e.status == "ongoing"){
                    btnChangeFn();
                    function btnChangeFn(cbfun){
                        if (window.parent) {
                            if(window.parent.JTCustom.isFormAvailable()){
                                typeof cbfun === "function" ? cbfun() : "";
                                $(".operation-intro-continue").removeClass("disabled-btn");
                            }else {
                                $(".tip").text("数据加载中，请稍后重试。");
                            }
                        }
                    }
                    let timerChangeBtn = setInterval(() => {
                        btnChangeFn(function(){
                            clearInterval(timerChangeBtn)
                        })
                    }, 1000);
                }
            });
        });
    }
    getNotice(){
        let noticetimer = setInterval(() => {
            setNotice(function(){
                clearInterval(noticetimer)
            })
        }, 1000);
        setNotice();
        function setNotice(cbfun){
            let getNotice = window.parent.JTCustom.getNotice();
            if (getNotice.text) {
                $(".exam-rules .ct .info-list").html(`${getNotice.text}`);
                typeof cbfun === "function" ? cbfun() : "";
            }
        }
    }
    bindFun() {
        let _this = this;
        $(".operation-intro-cancel").bind("click",function(){
            $(".exam-rules").show();
            $(".operation-intro").hide();
        });
        $(".operation-intro-continue").bind("click",function(){
            $(".tip").text("");
            if(!$(".operation-intro-continue").hasClass("disabled-btn")){
                window.parent.JTCustom.getSession().then(function(e){
                    if(e.status == "ongoing") {
                        if(window.parent.JTCustom.isFormAvailable()) {
                            window.parent.JTCustom.startExam();
                        } else {
                            $(".tip").text("数据加载中，请稍后重试。");
                        }
                    }
                });
            }
        });
        _this.bindExamNoticeBtn();
    }
}
(() => {
        console.log("notice file is run");
        let selfClass = new noticeClass();
        selfClass.init();
    }
)();
