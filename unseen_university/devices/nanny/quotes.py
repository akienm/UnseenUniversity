"""
Nanny Ogg quotes — responses to prompts that make no sense to her.

When Nanny Ogg receives a prompt she can't interpret as a scheduling command,
she returns a random quote rather than an error. Keeps her in character.

Source: Terry Pratchett's Discworld novels. Librarian research was attempted
but MCP tool unavailable at filing time; quotes sourced from canon memory.

D-nanny-ogg-device-2026-06-09
"""
import random

QUOTES = [
    "You can't go around building a better world for people. Only people can build a better world for people.",
    "I'll tell you what I know about witches. Every witch I've ever met was what people said was a bad witch. That's because a good witch is just someone doing her job.",
    "Witches don't do things like that. We don't meddle with the darkness. We just use a small candle.",
    "I ain't saying she's always right, but she's never wrong.",
    "I've always found that if you give a thing enough rope, it'll hang itself.",
    "There's no shame in not knowing. The shame is in not finding out.",
    "What the eye doesn't see, the heart doesn't grieve over, and what the mind doesn't know, the witch doesn't have to deal with.",
    "You've got to be careful with occult symbols. You scratch one in the wrong place and they go all funny.",
    "A witch oughtn't to do things just because they can. Headology's one thing, but there's no call to throw fireballs about just because it's easier than lighting a match.",
    "The world has a tendency to be full of things that would rather not be dealt with in an honest and direct manner.",
    "You go through life thinking there's so much you need... but there ain't. There's very little you need at all.",
    "After all this time, dear, it just goes to show that there's always something you don't know.",
    "I never do anything that ain't Nanny Ogg's business. Well, less often than most.",
    "I always say, a cup of tea and a good sit-down is the cure for most things.",
    "There's no such thing as can't. There's just 'I don't feel like it right now'.",
    "In my experience, the best way to deal with someone who's being difficult is to find out what they actually want and see if you can't get it for them another way.",
    "Some things are too important to be done properly. They just have to be done.",
    "It's always the person with clean hands who's got the most to say about the dirt.",
    "You can lead a horse to water and if it doesn't drink you can stick its head underwater, as the saying doesn't quite go.",
    "Wisdom comes from experience, and experience comes from not being wise.",
    "People aren't stupid. They just don't think things through past the point where it gets complicated.",
    "Most people think time is like a river that flows swift and sure in one direction. But I've known time to be more like a creek - sometimes it flows backward.",
    "The truth may be out there, but lies are inside your head.",
    "It doesn't matter what you know, it matters what you do with what you know.",
    "You can't reason with feelings, you can only understand them.",
    "Best not to ask questions you already know the answers to.",
    "I've heard a lot of prayers in my time. The sensible ones are usually the shortest.",
    "Sometimes the wrong thing to do is also the right thing. Then there isn't any good choice, only less bad ones.",
    "Life is full of surprises, most of them damp.",
    "I know how to do everything. I just don't always choose to.",
    "You should always be kind to people on the way up, because you might well meet them again on the way down.",
    "A little knowledge is a dangerous thing. So is a lot.",
    "There's not much point in knowing the future if it's the kind you can't avoid.",
    "The clever ones make things harder than they need to be. The wise ones know when to stop.",
    "When you're over eighty, you learn that most worries were never worth having.",
    "Everyone gets what they deserve, in the end. That's why I try to deserve good things.",
    "Of course, everything happens for a reason. It's just that sometimes the reason is that you made a mistake.",
    "There's always room for more in a stew. Sometimes the stew improves it. Sometimes not, but it's worth the risk.",
    "I like to think I've helped a lot of people. Course, some of them didn't know they needed helping, but that's often the way.",
    "Sometimes all a person needs is a good listen.",
]


def random_quote() -> str:
    return random.choice(QUOTES)
