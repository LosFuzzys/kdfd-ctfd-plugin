from flask import render_template, Blueprint
from flask import Flask, request, Response

from CTFd.plugins.challenges import CHALLENGE_CLASSES, BaseChallenge
from CTFd.plugins import register_plugin_assets_directory
import requests
import logging

blueprint = Blueprint("kdfd", __name__, template_folder="templates")

patched_challenge_classes = {}

@blueprint.route("/admin/kdfd")
def admin_view():
    return render_template("kdfd_config.html")

@blueprint.route("/api/v1/kdfd/challenge/<id>", methods=['GET', 'PUT', 'DELETE'])
def challenge(id):
    return {"success": id, "method": request.method}

@blueprint.route("/plugins/kdfd/<class_name>/inject.js")
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
    app.register_blueprint(blueprint)
    patch_challenge_classes()
