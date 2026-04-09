import { saveSessionUser } from "./JS_members.js";

/*==================================================================
    [ Focus input ]*/
    $('.input').each(function(){
        $(this).on('blur', function(){
            if($(this).val().trim() != "") {
                $(this).addClass('has-val');
            }
            else {
                $(this).removeClass('has-val');
            }
        })    
    })
  
  
/*==================================================================
    [ Validate ]*/
   
    var input = $('.validate-input .input');

    $('#loginForm').on('submit', async function(e){
        e.preventDefault(); // Prevent standard HTTP post
        var check = true;

        for(var i=0; i<input.length; i++) {
            if(validate(input[i]) == false){
                showValidate(input[i]);
                check=false;
            }
        }

        if(check) {
            const loginId = $('#loginId').val().trim();
            const password = $('#loginPassword').val().trim();
            const statusMsg = $('#loginStatus');

            statusMsg.text('Logging in...').css('color', 'black');

            try {
                const response = await fetch('/api/login', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ loginId, password })
                });
                const responseText = await response.text();
                let result;

                try {
                    result = JSON.parse(responseText);
                } catch (parseError) {
                    console.error("Invalid login response:", responseText);
                    throw new Error('The live login service returned an invalid response.');
                }

                if (!response.ok || !result.ok) {
                    statusMsg.text(result.message || 'Invalid login ID or password.').css('color', 'red');
                    return;
                }

                saveSessionUser({
                    ...result.user,
                    session_id: result.sessionId
                });
                statusMsg.text(`Welcome back, ${result.user.name}! Redirecting...`).css('color', 'green');

                // Warm the next views so the account and library tabs feel faster after login.
                Promise.allSettled([
                    fetch('/api/account'),
                    fetch('/api/books')
                ]);

                setTimeout(() => {
                    window.location.href = result.redirectPath || "/LoginPage/dashboard.html";
                }, 1000);
            } catch (error) {
                console.error("Error logging in: ", error);
                statusMsg.text(error.message || 'Unable to reach the live login service right now. Please try again in a moment.').css('color', 'red');
            }
        }
    });


    $('.validate-form .input').each(function(){
        $(this).focus(function(){
           hideValidate(this);
        });
    });

    function validate (input) {
        if($(input).attr('type') == 'email' || $(input).attr('name') == 'email') {
            if($(input).val().trim().match(/^([a-zA-Z0.]+)@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.)|(([a-z9\-]+\.)+))([a-zA-Z]{1,5}|[0-9]{1,3})(\]?)$/) == null) {
                return false;
            }
        }
        else {
            if($(input).val().trim() == ''){
                return false;
            }
        }
    }

    function showValidate(input) {
        var thisAlert = $(input).parent();

        $(thisAlert).addClass('alert-validate');
    }

    function hideValidate(input) {
        var thisAlert = $(input).parent();

        $(thisAlert).removeClass('alert-validate');
    }
    




