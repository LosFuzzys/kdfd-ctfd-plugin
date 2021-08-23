from flask import render_template, Blueprint
from flask import Flask, request, Response

from CTFd.plugins.challenges import CHALLENGE_CLASSES, BaseChallenge
from CTFd.utils.decorators import admins_only, during_ctf_time_only, require_verified_emails
from CTFd.utils.decorators.visibility import check_challenge_visibility
from CTFd.plugins import register_plugin_assets_directory
from CTFd.utils.plugins import register_script
from CTFd.models import Challenges
from CTFd.utils.decorators import ratelimit
import requests
import logging
from CTFd.utils.user import (
    #authed,
    #get_current_team,
    #get_current_team_attrs,
    get_current_user,
    #get_current_user_attrs,
    #is_admin,
)

kdfd = Blueprint("kdfd", __name__, template_folder="templates")

patched_challenge_classes = {}


@kdfd.route("/admin/kdfd")
@admins_only
def admin_view():
    return render_template("kdfd_config.html")


@kdfd.route("/api/v1/kdfd/challenge/<challenge_id>", methods=['GET', 'PUT', 'DELETE'])
@ratelimit(method="POST", limit=5, interval=10)
@check_challenge_visibility
@during_ctf_time_only
@require_verified_emails
def update_challenge(challenge_id):
    challenge = Challenges.query.filter_by(id=challenge_id).first_or_404()
    user = get_current_user()
    ctf_name = 'testctf'
    app_name = 'test_challenge_1i'
    instance_id = user.id
    server = 'http://kdfd.104.248.26.251.nip.io'
    cookies = {'auth-token': 'ct4m099034tfsst-0tmh-thi09'}
    if request.method == 'GET':
        res = requests.get(f'{server}/api/v1/ctfs/{ctf_name}/apps/{app_name}/instances/{instance_id}', cookies=cookies)
        logging.error(res.text)
        res = res.json()
        if not res['success']:
            return {"success": False, "msg": "Could not retrieve instance state", "res": res}
        if not res['instance']:
            return {"success": True, "response": res, "active": False}
        expiry = res["instance"]["expiry"]
    elif request.method == 'PUT':
        res = requests.put(f'{server}/api/v1/ctfs/{ctf_name}/apps/{app_name}/instances/{instance_id}', cookies=cookies, timeout=5) # TODO make configurable and handle exception
        logging.error(res.text)
        res = res.json()
        if not res['success']:
            return {"success": False, "response": res}
        expiry = res["instance"]["expiry"]
    else: #request.method == 'DELETE':
        res = requests.delete(f'{server}/api/v1/ctfs/{ctf_name}/apps/{app_name}/instances/{instance_id}', cookies=cookies)
        logging.error(res.text)
        res = res.json()
        if not res['success']:
            return {"success": False, "response": res}
        return {"success": True, "active": False, "response": res}
    # TODO challenge_name = parse(challenge.comments) (parse slug from db)
    challenge_name = 'chal1'
    res = requests.get(f'{server}/api/v1/ctfs/{ctf_name}/apps/{app_name}/instances/{instance_id}/challenge', params={'challenge_name': challenge_name}, cookies=cookies)
    logging.error(res.text)
    res = res.json()
    if not res['success']:
        return {"success": False, "msg": "Could not retrieve challenge connection info.", "res": res}
    connection_info_html = res.get("connection_info_html", 'error')
    return {"success": True, "active": True, "response": res, "connection_info_html": connection_info_html, "expiry": expiry}


@kdfd.route("/plugins/kdfd/<class_name>/inject.js")
def inject(class_name):
    res = requests.get(f'http://0:8000/{patched_challenge_classes[class_name]["view.js"]}')
    js_content = f'{res.text}\n\n{render_template("kdfd_challenge_view.js")}' # TODO does this need to be a template?
    return Response(js_content, mimetype='application/javascript; charset=utf-8')


def patch_challenge_classes():
    # TODO might be necessary to wait until all challanges have been loaded
    for class_name, challenge_class in CHALLENGE_CLASSES.items():
        try:
            patched_challenge_classes[class_name] = {
                "view.js": challenge_class.scripts["view"]
            }
            challenge_class.scripts["view"] = f'/plugins/kdfd/{class_name}/inject.js'
        except Exception:
            logging.warn('could not patch challenge class {class_name}')

def load(app):
    register_plugin_assets_directory(app, base_path="/plugins/kdfd-ctfd-plugin/assets/")
    register_script('test') # TODO set correct url
    app.register_blueprint(kdfd)
    patch_challenge_classes()
