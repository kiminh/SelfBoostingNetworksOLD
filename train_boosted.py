import functools

import numpy as np
import tensorflow as tf

import boosted_classifier
import boosting_strategy
import data.data as data
import metrics
import util


def run(stem_fn,
        block_fn,
        classifier_fn,
        voting_strategy_fn,
        boosting_strategy_fn,
        training_style,
        epochs,
        batch_size,
        block_num,
        dataset_name,
        classes,
        metrics_options,
        log_dir,
        load_stem=None,
        patience=12,
        progressive_training_epochs=5):
    """
    Args:
        load_stem (str): path to the weights file for the stem
    """
    metrics.setup_log_files(log_dir, block_num, metrics_options)

    # load data
    train_gen, validate_gen, train_data_shape, validate_data_shape, label_shape, class_num = data.load_data(
        dataset_name, batch_size, classes)

    data_ph, label_ph, _, weak_logits, classifier, classification_metrics = boosted_classifier.build_model(
        stem_fn,
        block_fn,
        classifier_fn,
        block_num,
        voting_strategy_fn,
        batch_size,
        class_num,
        train_data_shape,
        label_shape,
        load_stem=load_stem)


    stem_saver = tf.train.Saver(
        tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='stem'))
    weighted_losses = boosting_strategy.calculate_boosted_losses(
        boosting_strategy_fn, weak_logits, label_ph, batch_size, class_num)

    weights_scale_ph = tf.placeholder_with_default(
        tf.ones([block_num]), [block_num])

    def feed_dict_fn(epoch):
        data, labels = next(train_gen)
        feed_dict = {data_ph: data, label_ph: labels}
        if training_style == 'progressive':
            val = np.zeros([block_num], dtype=np.float32)
            val[epoch // 2] = 1.
            val[(epoch // 2) - 1] = 0.
            feed_dict[weights_scale_ph] = val
        return feed_dict

    def validate_feed_dict_fn():
        data, labels = next(validate_gen)
        feed_dict = {data_ph: data, label_ph: labels}
        return feed_dict

    # calculate gradients
    optimizer = tf.train.AdamOptimizer()
    final_grads_and_vars, grad_metrics = boosting_strategy.calculate_boosted_gradients(
        optimizer, weighted_losses, weights_scale_ph)
    train_op = optimizer.apply_gradients(final_grads_and_vars)

    # if the voting strategy has an update fn, use it
    # I, for one, welcome our new duck typing overlords
    if hasattr(classifier.voting_strategy, 'update'):
        voting_strategy_update_op = classifier.voting_strategy.update(
            weak_logits, label_ph)
        train_op = tf.group(train_op, voting_strategy_update_op)

    print("Trainable Parameters: {}".format(
        np.sum([
            np.prod(v.get_shape().as_list()) for v in tf.trainable_variables()
        ])))

    verbose_ops_dict = classification_metrics
    if 'gradient_norms' in metrics_options:
        verbose_ops_dict.update(grad_metrics)

    # initialize session and train
    process_metrics_fn = functools.partial(
        metrics.process_metrics, log_dir=log_dir, options=metrics_options)
    early_stopping_fn = util.build_early_stopping_fn(patience=patience)
    full_metrics = util.train(
        train_op,
        epochs,
        train_steps_per_epoch=train_data_shape[0] // batch_size,
        validate_steps_per_epoch=validate_data_shape[0] // batch_size,
        verbose_ops_dict=verbose_ops_dict,
        train_feed_dict_fn=feed_dict_fn,
        validate_feed_dict_fn=validate_feed_dict_fn,
        process_metrics_fn=process_metrics_fn,
        early_stopping_fn=early_stopping_fn,
        stem_saver=stem_saver,
        stem=load_stem)

    return full_metrics
