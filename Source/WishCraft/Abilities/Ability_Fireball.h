// Offense slot: spawns a server-authoritative projectile.
#pragma once

#include "CoreMinimal.h"
#include "Abilities/AbilityComponentBase.h"
#include "Ability_Fireball.generated.h"

UCLASS()
class UAbility_Fireball : public UAbilityComponentBase
{
	GENERATED_BODY()

public:
	virtual float BeginEffect() override;
};
