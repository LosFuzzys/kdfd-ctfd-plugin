
var kdfd_old_renderer = CTFd._internal.challenge.postRender;

CTFd._internal.challenge.postRender = function () {
    kdfd_old_renderer();
    $ = CTFd.lib.$;

    kdfd_chal = $('.kdfd-chal');
    create_button = kdfd_chal.find('.kdfd-create');
    destroy_button = kdfd_chal.find('.kdfd-destroy');
    refresh_button = kdfd_chal.find('.kdfd-refresh');
    loading_info = kdfd_chal.find('.kdfd-loading');
    link_text = kdfd_chal.find('.kdfd-link');
    alert_box = kdfd_chal.find('.kdfd-alert');
    timout_text = kdfd_chal.find('.kdfd-timeout');

    challenge_api = function(http_method, callback) {
        CTFd.fetch("/api/v1/kdfd/challenge/" + CTFd._internal.challenge.data.id, {
            method: http_method
          })
        .then(response => response.json())
        .then(callback);
    }

    on_challenge_response = function(data) {
        // TODO remove debug
        console.log('response');
        console.log(data);
        loading_info.hide();
        if (!data["success"]) {
            alert(data);
            alert_box.show();
            alert_box.html(data["msg"]);
        }
        var connection_info_html = 'You have no active instance';
        var expiry = false;
        if (data["active"]) {
            console.debug('active');
            connection_info_html = data["connection_info_html"];
            expiry = data["expiry"];
            create_button.attr('disabled', "");
            destroy_button.removeAttr('disabled');
            refresh_button.removeAttr('disabled');
        } else {
            console.debug('in-active');
            create_button.removeAttr('disabled');
            destroy_button.attr('disabled', "");
            refresh_button.removeAttr('disabled');
        }

        create_button.find('.kdfd-button-text').html('Create');
        create_button.find('.kdfd-button-spinner').hide();
        destroy_button.find('.kdfd-button-text').html('Stop');
        destroy_button.find('.kdfd-button-spinner').hide();

        if (!connection_info_html) {
            connection_info_html = 'retrieving...';
            setTimeout(function(){ 
                refresh_button.trigger('click');
             }, 1000);
        }

        link_text.html(connection_info_html);
        if (expiry) {
            expiry_date = new Date(expiry).toLocaleString();
            timout_text.html(expiry_date);
        } else {
            timout_text.html('');
        }

    }

    check_challenge = function() {
        challenge_api('GET', on_challenge_response);
    }

    create_challenge = function() {
        challenge_api('PUT', on_challenge_response);
    }

    destroy_challenge = function() {
        challenge_api('DELETE', data =>
            console.log(data)
        );
    }

    create_button.click(function() {
        loading_info.show();
        create_button.attr('disabled', "");
        create_challenge();
    });
    destroy_button.click(function() {
        loading_info.show();
        destroy_button.attr('disabled', "");
        destroy_challenge();
    });
    refresh_button.click(function() {
        loading_info.show();
        refresh_button.attr('disabled', "");
        check_challenge();
    });

    $(function () {
        $('[data-toggle="popover"]').popover()
    })

    loading_info.hide();
    refresh_button.trigger('click');
};