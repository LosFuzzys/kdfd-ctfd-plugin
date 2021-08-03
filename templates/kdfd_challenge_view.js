
var kdfd_old_renderer = CTFd._internal.challenge.postRender;

CTFd._internal.challenge.postRender = function () {
    kdfd_old_renderer();
    $ = CTFd.lib.$;

    kdfd_chal = $('#kdfd-chal');
    create_button = kdfd_chal.find('.kdfd-create');
    destroy_button = kdfd_chal.find('.kdfd-destroy');
    link_text = kdfd_chal.find('.kdfd-link');
    timout_text = kdfd_chal.find('.kdfd-timout');

    challenge_api = function(http_method, callback) {
        CTFd.fetch("/api/v1/kdfd/challenge/" + CTFd._internal.challenge.data.id, {
            method: http_method
          })
        .then(response => response.json())
        .then(callback);
    }

    on_challenge_response = function(data) {
        var link = 'unknown';
        var time = 'unknown'
        console.log('response');
        console.log(data);
        if (data["status"] == "ok") {
            link = data["link"];
            time = data["time"];
        }
        link_text.html(link);
        timout_text.html(time);
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
        create_button.attr('disabled', "");
        create_button.find('.kdfd-button-text').html('Creating Instance...');
        create_button.find('.kdfd-button-spinner').show();
        create_challenge();
    });
    destroy_button.find('.kdfd-destroy').click(function() {
        destroy_button.attr('disabled', "");
        destroy_button.find('.kdfd-button-text').html('Creating Instance...');
        destroy_button.find('.kdfd-button-spinner').show();
    });
    kdfd_chal.find('.kdfd-info').collapse('show');
};