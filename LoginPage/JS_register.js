import { saveSessionUser } from "./JS_members.js";

// Basic input animations and validation copied from JS_main.js
$('.input').each(function(){
    $(this).on('blur', function(){
        if($(this).val().trim() != "") {
            $(this).addClass('has-val');
        } else {
            $(this).removeClass('has-val');
        }
    })    
})

var input = $('.validate-input .input');

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
    } else {
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

async function populateNextMemberId() {
    try {
        const response = await fetch('/api/next-member-id');
        const result = await response.json();

        if (!response.ok || !result.ok) {
            throw new Error(result.message || 'Unable to generate member ID.');
        }

        $('#generatedMemberId').val(result.memberId);
    } catch (error) {
        console.error("Error loading members: ", error);
        $('#generatedMemberId').val('Unavailable');
    }
}

populateNextMemberId();

$('#registerForm').on('submit', async function(e){
    e.preventDefault(); // Prevent page reload
    var check = true;

    for(var i=0; i<input.length; i++) {
        if(validate(input[i]) == false){
            showValidate(input[i]);
            check=false;
        }
    }

    if(check) {
        const name = $('#regName').val().trim();
        const email = $('#regEmail').val().trim();
        const password = $('#regPassword').val().trim();
        const statusMsg = $('#statusMessage');
        
        statusMsg.text('Processing...').css('color', 'black');

        try {
            const response = await fetch('/api/register', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ name, email, password })
            });

            const result = await response.json();

            if (!response.ok || !result.ok) {
                statusMsg.text(result.message || 'Unable to create account.').css('color', 'red');
                return;
            }

            saveSessionUser(result.user);
            console.log("Account created with ID: ", result.user.user_id);
            statusMsg.text(`Account created! Your member ID is ${result.user.user_id}. Redirecting...`).css('color', 'green');

            setTimeout(() => {
                window.location.href = "dashboard.html";
            }, 1000);

        } catch (error) {
            console.error("Error adding document: ", error);
            statusMsg.text('Unable to save member data. Please use a local server and try again.').css('color', 'red');
        }
    }
});
